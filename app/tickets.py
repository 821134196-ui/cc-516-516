"""券订单：购票、预约、改期、转赠、退款。

并发约束（BEGIN IMMEDIATE 串行化）：
- 同一时刻一张券只能处于一个写事务中，因此“入园核销与改期不能同时成功”。
- 名额在同一事务内 COUNT + INSERT，超卖被回滚。
"""
import secrets
from datetime import timedelta

from . import catalog
from .clock import Clock
from .config import SLOTS
from .db import tx
from .errors import (CapacityError, ClosureError, FarmError,
                     TicketNotFound, TicketStateError)


def _gen_code():
    return "FARM-" + secrets.token_hex(3).upper()


def _log(conn, ticket_id, event, detail="", actor=""):
    conn.execute(
        "INSERT INTO ledger(ticket_id,event,detail,actor,created_at) "
        "VALUES(?,?,?,?,?)",
        (ticket_id, event, detail, actor, Clock().iso()),
    )


def get_ticket(conn, code):
    row = conn.execute(
        "SELECT * FROM tickets WHERE code=?", (code.strip().upper(),)
    ).fetchone()
    if not row:
        raise TicketNotFound("查无此券，请核对券码")
    return row


def active_booking(conn, ticket_id):
    return conn.execute(
        "SELECT * FROM bookings WHERE ticket_id=? AND status IN ('booked','checked_in') "
        "ORDER BY date DESC, slot DESC LIMIT 1",
        (ticket_id,),
    ).fetchone()


def purchase(conn, clock, type_id, holder_name, holder_phone,
             children=0, payment=None):
    tt = conn.execute("SELECT * FROM ticket_types WHERE id=?", (type_id,)).fetchone()
    if not tt:
        raise FarmError("票种不存在")
    crop = conn.execute("SELECT * FROM crops WHERE code=?", (tt["crop_code"],)).fetchone()
    code = _gen_code()
    now = clock.now()
    with tx(conn):
        pay_res = (payment or _NullPay()).charge(tt["price"], note="购票")
        if not pay_res["ok"]:
            raise FarmError("支付失败，未出票")
        cur = conn.execute(
            "INSERT INTO tickets(code,type_id,crop_code,zone_code,people,children,"
            "base_kg,unit_price,amount_paid,holder_name,holder_phone,valid_from,valid_until,"
            "created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (code, type_id, tt["crop_code"], tt["zone_code"], tt["people"],
             int(children), tt["base_kg"], crop["unit_price"], tt["price"],
             holder_name, holder_phone, now.strftime("%Y-%m-%d"),
             (now + timedelta(days=tt["valid_days"])).strftime("%Y-%m-%d"),
             clock.iso()),
        )
        tid = cur.lastrowid
        conn.execute(
            "INSERT INTO payments(ticket_id,kind,amount,ref_no,note,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (tid, "ticket", tt["price"], pay_res["ref_no"], "购票", clock.iso()),
        )
        _log(conn, tid, "purchase", f"出票 {code}", holder_name)
    return get_ticket(conn, code)


def book(conn, clock, ticket_code, date, slot, actor="visitor"):
    if slot not in SLOTS:
        raise FarmError("时段无效")
    with tx(conn):
        t = get_ticket(conn, ticket_code)
        if t["status"] in ("redeemed", "weighed"):
            raise TicketStateError("该券已入园核销，无法改期或重复预约")
        if t["status"] in ("refunded", "cancelled"):
            raise TicketStateError("该券已退，无法预约")
        if not (t["valid_from"] <= date <= t["valid_until"]):
            raise TicketStateError("预约日期超出券有效期")
        if catalog.park_closed(conn, date):
            raise ClosureError("当日雨天全园停采，请选择其他日期或申请退款")
        clo = catalog.zone_closed(conn, t["zone_code"], date)
        if clo:
            raise ClosureError(f"当日该区停采（{clo['reason']}），请改期或申请退款")
        if active_booking(conn, t["id"]):
            raise TicketStateError("已有进行中的预约，请先改期或取消")
        used = catalog.slot_used(conn, t["zone_code"], date, slot)
        if used >= 40:
            raise CapacityError("该时段名额已满")
        conn.execute(
            "INSERT INTO bookings(ticket_id,date,slot,created_at) VALUES(?,?,?,?)",
            (t["id"], date, slot, clock.iso()),
        )
        conn.execute("UPDATE tickets SET status='booked' WHERE id=?", (t["id"],))
        _log(conn, t["id"], "book", f"{date} {slot}", actor)
    return get_ticket(conn, ticket_code)


def cancel_booking(conn, clock, ticket_code, reason="visitor_cancel", actor="visitor"):
    with tx(conn):
        t = get_ticket(conn, ticket_code)
        b = active_booking(conn, t["id"])
        if not b:
            raise TicketStateError("没有进行中的预约")
        if b["status"] == "checked_in":
            raise TicketStateError("已入园，不能取消预约")
        conn.execute("UPDATE bookings SET status='cancelled' WHERE id=?", (b["id"],))
        conn.execute("UPDATE tickets SET status='valid' WHERE id=?", (t["id"],))
        _log(conn, t["id"], "cancel", reason, actor)


def reschedule(conn, clock, ticket_code, new_date, new_slot,
               reason="visitor", actor="visitor"):
    """改期：旧预约置 rescheduled，同事务建新预约。

    与 redeem 共用票行的写事务：任一方先拿到写锁，另一方必读到最新状态，
    因此核销与改期不可能同时成功。
    """
    if new_slot not in SLOTS:
        raise FarmError("时段无效")
    with tx(conn):
        t = get_ticket(conn, ticket_code)
        if t["status"] in ("redeemed", "weighed"):
            raise TicketStateError("该券已核销入园，不能改期")
        b = active_booking(conn, t["id"])
        if not b:
            raise TicketStateError("没有进行中的预约，无法改期")
        if catalog.park_closed(conn, new_date):
            raise ClosureError("目标日期雨天全园停采")
        clo = catalog.zone_closed(conn, t["zone_code"], new_date)
        if clo:
            raise ClosureError("目标日期该区停采")
        used = catalog.slot_used(conn, t["zone_code"], new_date, new_slot)
        if used >= 40:
            raise CapacityError("目标时段名额已满")
        conn.execute("UPDATE bookings SET status='rescheduled' WHERE id=?", (b["id"],))
        conn.execute(
            "INSERT INTO bookings(ticket_id,date,slot,created_at) VALUES(?,?,?,?)",
            (t["id"], new_date, new_slot, clock.iso()),
        )
        _log(conn, t["id"], "reschedule",
             f"{b['date']} {b['slot']} -> {new_date} {new_slot} ({reason})", actor)
    return get_ticket(conn, ticket_code)


def transfer(conn, clock, ticket_code, new_name, new_phone, actor="visitor"):
    """券转赠：未使用的券可改持有人，留痕原持有人。"""
    with tx(conn):
        t = get_ticket(conn, ticket_code)
        if t["status"] in ("redeemed", "weighed"):
            raise TicketStateError("已核销入园的券不能转赠")
        if t["status"] in ("refunded", "cancelled"):
            raise TicketStateError("已退券不能转赠")
        conn.execute(
            "UPDATE tickets SET holder_name=?, holder_phone=?, gifted_from=? WHERE id=?",
            (new_name, new_phone,
             (t["gifted_from"] or f"{t['holder_name']}/{t['holder_phone']}"),
             t["id"]),
        )
        _log(conn, t["id"], "gift", f"转赠给 {new_name}", actor)
    return get_ticket(conn, ticket_code)


def refund_ticket(conn, clock, ticket_code, amount, reason, actor="manager",
                  payment=None):
    """通用退款（停采退、雨天退等）。已称重离园的券不退票款。"""
    with tx(conn):
        t = get_ticket(conn, ticket_code)
        if t["status"] in ("refunded", "weighed"):
            raise TicketStateError("当前状态不允许退款")
        pay = payment or _NullPay()
        res = pay.refund(round(amount, 2))
        if not res["ok"]:
            raise FarmError("退款通道异常，请稍后重试")
        b = active_booking(conn, t["id"])
        if b:
            conn.execute(
                "UPDATE bookings SET status='force_refunded' WHERE id=?", (b["id"],)
            )
        conn.execute("UPDATE tickets SET status='refunded' WHERE id=?", (t["id"],))
        conn.execute(
            "INSERT INTO payments(ticket_id,kind,amount,ref_no,note,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (t["id"], "refund", -round(amount, 2), res["ref_no"], reason, clock.iso()),
        )
        _log(conn, t["id"], "refund", f"{reason} 退 {amount:.2f}", actor)


class _NullPay:
    """测试 / 纯查询场景下未显式注入支付时的兜底（不应被扣款路径触达）。"""
    def charge(self, *a, **k):
        raise FarmError("未配置支付适配器")

    def refund(self, *a, **k):
        raise FarmError("未配置支付适配器")
