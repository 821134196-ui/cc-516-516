"""现场运营：入口核销、称重结算、离园后补扣款。

关键规则：
- 核销/称重全部走 BEGIN IMMEDIATE，两入口并发扫同一张券只有一个成功；
- 重量只来自电子秤适配器，任何 HTTP 参数都不能写入重量，检票员无权修改；
- 现场支付失败不拦游客离场：转为欠账（debts），离园后可补扣。
"""
from datetime import datetime, date

from . import catalog
from .adapters import ScaleError  # noqa: F401  (便于上层统一导入)
from .config import CHILD_FREE_KG
from .db import tx
from .errors import FarmError, TicketStateError
from .tickets import _log, get_ticket


class ReplayError(FarmError):
    """券重复入园 / 重放。附带已核销现场信息用于现场端提示。"""
    def __init__(self, msg, info=None):
        super().__init__(msg)
        self.info = info or {}


def open_entry(conn, ticket_id):
    return conn.execute(
        "SELECT * FROM entries WHERE ticket_id=? AND exited_at IS NULL",
        (ticket_id,),
    ).fetchone()


def inspect(conn, code):
    """核销前预览券状态（不加锁，供现场端展示）。"""
    t = get_ticket(conn, code)
    b = conn.execute(
        "SELECT * FROM bookings WHERE ticket_id=? "
        "AND status IN ('booked','checked_in') ORDER BY id DESC LIMIT 1",
        (t["id"],),
    ).fetchone()
    e = open_entry(conn, t["id"])
    zone = conn.execute("SELECT * FROM zones WHERE code=?", (t["zone_code"],)).fetchone()
    crop = conn.execute("SELECT * FROM crops WHERE code=?", (t["crop_code"],)).fetchone()
    return {"ticket": t, "booking": b, "entry": e, "zone": zone, "crop": crop}


def redeem(conn, clock, code, gate):
    today = clock.date_iso()
    now = clock.now()
    with tx(conn):
        t = get_ticket(conn, code)

        # 1) 已入园 / 已离园：拒绝重放，回传首次入园信息
        if t["status"] in ("redeemed", "weighed"):
            first = conn.execute(
                "SELECT * FROM entries WHERE ticket_id=? ORDER BY id LIMIT 1",
                (t["id"],),
            ).fetchone()
            w = conn.execute(
                "SELECT * FROM weighings WHERE ticket_id=?", (t["id"],)
            ).fetchone()
            info = {"gate": first["gate"] if first else "?",
                    "entered_at": first["entered_at"] if first else "",
                    "weighed": bool(w)}
            raise ReplayError(
                f"该券已于 {info['entered_at']} 在{_gate_name(info['gate'])}入园，"
                + ("且已结算离园，" if w else "仍在园内，")
                + "不能重复核销", info)

        if t["status"] in ("refunded", "cancelled"):
            raise TicketStateError("该券已退，不可入园")

        b = conn.execute(
            "SELECT * FROM bookings WHERE ticket_id=? AND status='booked' "
            "ORDER BY id DESC LIMIT 1", (t["id"],),
        ).fetchone()
        if not b:
            raise TicketStateError("该券未预约，请先在游客端选择时段")
        if b["date"] != today:
            raise TicketStateError(
                f"预约日期为 {b['date']} {b['slot']}，今日不可核销，请先改期")

        # 2) 当天停采（雨天 / 临停）：拦住并提示退改
        park = catalog.park_closed(conn, today)
        if park:
            raise TicketStateError(
                f"今日雨天全园停采（{park['note'] or '雨天停园'}），请到游客端办理改期或全额退款")
        clo = catalog.zone_closed(conn, t["zone_code"], today)
        if clo:
            raise TicketStateError(
                f"所属园区今日临时停采，请改期或联系管理员退款")

        # 3) 时段窗口（开场前 30 分钟 ~ 时段结束后 120 分钟宽限）
        #    演示环境常在非营业时间体验：营业时段外（08:00 前 / 19:30 后）
        #    放宽为“当日券均可核销”，营业时间内严格按时段。
        start, end = b["slot"].split("-")
        sh, sm = map(int, start.split(":"))
        eh, em = map(int, end.split(":"))
        win_lo = now.replace(hour=sh, minute=sm) - _mins(30)
        win_hi = now.replace(hour=eh, minute=em) + _mins(120)
        operating = now.replace(hour=8, minute=0) <= now <= now.replace(hour=19, minute=30)
        if operating and not (win_lo <= now <= win_hi):
            raise TicketStateError(f"不在可核销时段（预约 {b['slot']}）")

        # 4) 防御：同券不得存在未离场入园记录
        if open_entry(conn, t["id"]):
            raise ReplayError("该券已有未离场入园记录，不能重复入园")

        cur = conn.execute(
            "INSERT INTO entries(ticket_id,booking_id,gate,entered_at) "
            "VALUES(?,?,?,?)",
            (t["id"], b["id"], gate, clock.iso()),
        )
        conn.execute("UPDATE bookings SET status='checked_in' WHERE id=?", (b["id"],))
        conn.execute("UPDATE tickets SET status='redeemed' WHERE id=?", (t["id"],))
        _log(conn, t["id"], "redeem", f"{_gate_name(gate)} 核销入园", f"gate:{gate}")
        entry_id = cur.lastrowid
    return inspect(conn, code) | {"entry_id": entry_id}


def weigh_and_checkout(conn, clock, code, scale, payment):
    """离园称重结算。重量只取自 scale，函数不接受任何手工重量入参。"""
    gross = scale.read()                       # 断连在此抛出 ScaleError
    with tx(conn):
        t = get_ticket(conn, code)
        if t["status"] == "weighed":
            w = conn.execute(
                "SELECT * FROM weighings WHERE ticket_id=?", (t["id"],)).fetchone()
            raise TicketStateError(
                f"该券已结算（{w['gross_kg']}kg / ¥{w['amount']:.2f}），称重结果不可修改")
        if t["status"] != "redeemed":
            raise TicketStateError("该券尚未入园核销，不能称重")
        e = open_entry(conn, t["id"])
        if not e:
            raise TicketStateError("入园记录异常")
        if conn.execute("SELECT 1 FROM weighings WHERE ticket_id=?", (t["id"],)).fetchone():
            raise TicketStateError("称重记录已存在且锁定")

        free_kg = round(t["base_kg"] + t["children"] * CHILD_FREE_KG, 3)
        charge_kg = round(max(0.0, gross - free_kg), 3)
        amount = round(charge_kg * t["unit_price"], 2)

        conn.execute(
            "INSERT INTO weighings(ticket_id,entry_id,gross_kg,free_kg,charge_kg,"
            "amount,weighed_at,locked) VALUES(?,?,?,?,?,?,?,1)",
            (t["id"], e["id"], gross, free_kg, charge_kg, amount, clock.iso()),
        )

        debt = None
        if amount > 0:
            res = payment.charge(amount, note="超额重量")
            conn.execute(
                "INSERT INTO payments(ticket_id,kind,amount,channel,status,ref_no,"
                "note,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (t["id"], "extra", amount, "mock",
                 "ok" if res["ok"] else "failed", res["ref_no"],
                 "超额重量结算" + ("" if res["ok"] else "（扣款失败转欠账）"),
                 clock.iso()),
            )
            if not res["ok"]:
                cur = conn.execute(
                    "INSERT INTO debts(ticket_id,amount,reason,created_at) "
                    "VALUES(?,?,?,?)",
                    (t["id"], amount, "现场支付失败，离园后补扣", clock.iso()),
                )
                debt = {"id": cur.lastrowid, "amount": amount}
        conn.execute("UPDATE entries SET exited_at=? WHERE id=?",
                     (clock.iso(), e["id"]))
        conn.execute("UPDATE tickets SET status='weighed' WHERE id=?", (t["id"],))
        _log(conn, t["id"], "weigh",
             f"毛重 {gross}kg 免费 {free_kg}kg 计费 {charge_kg}kg ¥{amount:.2f}"
             + (" 补扣失败挂账" if debt else ""), "staff")

    return {
        "gross_kg": gross, "free_kg": free_kg,
        "charge_kg": charge_kg, "amount": amount, "debt": debt,
    }


def open_debts(conn):
    return conn.execute(
        "SELECT d.*, t.code tcode, t.holder_name, t.holder_phone "
        "FROM debts d JOIN tickets t ON t.id=d.ticket_id "
        "WHERE d.status='open' ORDER BY d.id"
    ).fetchall()


def settle_debt(conn, clock, debt_id, payment):
    """离园后补扣款重试。"""
    with tx(conn):
        d = conn.execute("SELECT * FROM debts WHERE id=?", (debt_id,)).fetchone()
        if not d:
            raise FarmError("欠账记录不存在")
        if d["status"] != "open":
            raise TicketStateError("该笔欠账已处理")
        res = payment.charge(d["amount"], note="离园后补扣")
        if not res["ok"]:
            raise FarmError("补扣仍失败，可稍后再次尝试")
        conn.execute(
            "UPDATE debts SET status='collected', paid_at=?, pay_ref=? WHERE id=?",
            (clock.iso(), res["ref_no"], debt_id),
        )
        conn.execute(
            "INSERT INTO payments(ticket_id,kind,amount,ref_no,note,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (d["ticket_id"], "arrears", d["amount"], res["ref_no"],
             "离园后补扣成功", clock.iso()),
        )
        _log(conn, d["ticket_id"], "arrears",
             f"补扣 ¥{d['amount']:.2f} 成功 {res['ref_no']}", "manager")
        return res["ref_no"]


def _gate_name(g):
    return {"east": "东入口", "west": "西入口", "north": "北入口"}.get(g, g)


def _mins(n):
    from datetime import timedelta
    return timedelta(minutes=n)
