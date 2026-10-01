"""领域服务：采摘券全流程业务规则。

所有写操作在进程锁 + SQLite 事务内完成，关键表上还有唯一约束兜底，
保证核销/改期互斥、跨入口不可重复入园、停采名额回收等并发正确性。
"""
from __future__ import annotations

import secrets
import sqlite3
from datetime import date as _date
from datetime import datetime, timedelta

from . import db
from .adapters import pay, scale, ScaleError

SLOTS = ["08:30-10:00", "10:00-11:30", "13:00-14:30", "14:30-16:00"]


class ServiceError(Exception):
    """业务规则错误（消息可直接展示给用户）。"""

    def __init__(self, message: str, code: str = "business_error", status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def today_str() -> str:
    return _date.today().isoformat()


def gen_code() -> str:
    return "PK" + secrets.token_hex(3).upper() + secrets.token_hex(1).upper()


class FarmService:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ---------------------------------------------------------------- 查询

    def _row(self, sql: str, params: tuple = ()) -> sqlite3.Row:
        r = self.conn.execute(sql, params).fetchone()
        if r is None:
            raise ServiceError("记录不存在", "not_found", 404)
        return r

    def get_ticket_type(self, type_id: int) -> sqlite3.Row:
        return self._row("SELECT * FROM ticket_types WHERE id=?", (type_id,))

    def get_zone(self, zone_id: int) -> sqlite3.Row:
        return self._row("SELECT * FROM zones WHERE id=?", (zone_id,))

    def get_ticket(self, code: str) -> sqlite3.Row:
        return self._row(
            "SELECT * FROM tickets WHERE code=? AND status!='transferred'",
            (code.strip().upper(),),
        )

    def list_crops_zones(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT z.*, c.name AS crop_name, c.emoji
               FROM zones z JOIN crops c ON c.id=z.crop_id ORDER BY z.id"""
        ).fetchall()

    def list_ticket_types(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT tt.*, c.name AS crop_name, c.emoji, z.name AS zone_name
               FROM ticket_types tt
               JOIN crops c ON c.id=tt.crop_id
               JOIN zones z ON z.id=tt.zone_id
               ORDER BY tt.id"""
        ).fetchall()

    def zone_day(self, zone_id: int, day: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM zone_day_status WHERE zone_id=? AND date=?",
            (zone_id, day),
        ).fetchone()

    def zone_open(self, zone_id: int, day: str) -> tuple[bool, str]:
        """返回 (当日是否可采, 原因)。日级状态覆盖分区默认状态。"""
        zds = self.zone_day(zone_id, day)
        if zds is not None:
            return zds["status"] == "open", zds["reason"]
        z = self.get_zone(zone_id)
        return z["status"] == "open", (z["close_reason"] or "")

    def day_booked(self, zone_id: int, day: str, exclude_ticket: int | None = None) -> int:
        """当日该分区有效占用名额的券数（含已入园者）。"""
        sql = ("SELECT COUNT(*) AS n FROM tickets "
               "WHERE zone_id=? AND valid_date=? "
               "AND status IN ('valid','used','checked_out')")
        params: list = [zone_id, day]
        if exclude_ticket is not None:
            sql += " AND id!=?"
            params.append(exclude_ticket)
        return self.conn.execute(sql, params).fetchone()["n"]

    def day_capacity(self, zone_id: int, day: str) -> int:
        r = self.conn.execute(
            "SELECT capacity FROM day_capacity WHERE zone_id=? AND date=?",
            (zone_id, day),
        ).fetchone()
        return r["capacity"] if r else 0

    # ---------------------------------------------------------- 采摘日历

    def calendar(self, date_from: str, days: int = 10) -> dict:
        """游客端：可预约日期 + 各分区剩余名额 + 停采/停园标记。"""
        out: dict[str, dict] = {}
        d0 = datetime.fromisoformat(date_from).date()
        zones = self.list_crops_zones()
        for i in range(days):
            day = (d0 + timedelta(days=i)).isoformat()
            zones_info = []
            park_open = True
            for z in zones:
                ok, reason = self.zone_open(z["id"], day)
                cap = self.day_capacity(z["id"], day)
                booked = self.day_booked(z["id"], day)
                if not ok:
                    park_open = False
                zones_info.append({
                    "zone_id": z["id"], "zone_name": z["name"],
                    "crop_name": z["crop_name"], "emoji": z["emoji"],
                    "open": ok, "reason": reason,
                    "capacity": cap, "booked": booked,
                    "remaining": max(0, cap - booked) if ok else 0,
                })
            out[day] = {"park_open": park_open, "zones": zones_info, "slots": SLOTS}
        return out

    # ---------------------------------------------------------- 购券

    def buy_ticket(self, type_id: int, valid_date: str, slot: str,
                   holder_name: str, holder_phone: str,
                   persons: int, children: int = 0) -> dict:
        with db.lock():
            tt = self.get_ticket_type(type_id)
            if valid_date not in tt["valid_dates"].split(","):
                raise ServiceError("该日期不在此券的可采日期范围内", "bad_date")
            ok, reason = self.zone_open(tt["zone_id"], valid_date)
            if not ok:
                raise ServiceError(f"当日不可采摘：{reason}", "zone_closed", 409)
            if persons < 1 or persons > tt["max_persons"]:
                raise ServiceError(f"入园人数需在 1~{tt['max_persons']} 之间")
            cap = self.day_capacity(tt["zone_id"], valid_date)
            if cap and self.day_booked(tt["zone_id"], valid_date) >= cap:
                raise ServiceError("当日名额已满", "sold_out", 409)
            if slot not in SLOTS:
                raise ServiceError("时段无效")
            code = gen_code()
            cur = self.conn.execute(
                """INSERT INTO tickets
                   (code,type_id,holder_name,holder_phone,persons,children,
                    status,valid_date,zone_id,created_at)
                   VALUES(?,?,?,?,?,?,'valid',?,?,?)""",
                (code, type_id, holder_name, holder_phone, persons, children,
                 valid_date, tt["zone_id"], now_iso()),
            )
            ticket_id = cur.lastrowid
            self.conn.execute(
                """INSERT INTO bookings (ticket_id,zone_id,date,slot,created_at)
                   VALUES(?,?,?,?,?)""",
                (ticket_id, tt["zone_id"], valid_date, slot, now_iso()),
            )
            pr = pay.charge(tt["price_cents"], "购券")
            self.conn.execute(
                """INSERT INTO transactions
                   (ticket_id,kind,amount_cents,status,ref,note,created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (ticket_id, "purchase", tt["price_cents"], pr.status,
                 pr.ref, pr.message, now_iso()),
            )
            if pr.status != "success":
                raise ServiceError("支付未成功，请重试", "pay_failed", 402)
            self.conn.commit()
            return {"code": code, "ticket_id": ticket_id}

    def ticket_detail(self, code: str) -> dict:
        t = self.get_ticket(code)
        tt = self.get_ticket_type(t["type_id"])
        z = self.get_zone(t["zone_id"])
        bk = self.conn.execute(
            "SELECT * FROM bookings WHERE ticket_id=? ORDER BY id DESC LIMIT 1",
            (t["id"],),
        ).fetchone()
        w = self.conn.execute(
            "SELECT * FROM weighings WHERE ticket_id=?", (t["id"],)
        ).fetchone()
        open_ok, reason = self.zone_open(t["zone_id"], t["valid_date"])
        txns = self.conn.execute(
            "SELECT * FROM transactions WHERE ticket_id=? ORDER BY id", (t["id"],)
        ).fetchall()
        return {
            "ticket": dict(t), "type": dict(tt), "zone": dict(z),
            "booking": dict(bk) if bk else None,
            "weighing": dict(w) if w else None,
            "zone_open_today": open_ok, "close_reason": reason,
            "transactions": [dict(x) for x in txns],
        }

    def my_tickets(self, phone: str) -> list[dict]:
        rows = self.conn.execute(
            """SELECT t.*, tt.name AS type_name, c.emoji, z.name AS zone_name,
                      b.slot AS slot
               FROM tickets t
               JOIN ticket_types tt ON tt.id=t.type_id
               JOIN crops c ON c.id=tt.crop_id
               JOIN zones z ON z.id=t.zone_id
               LEFT JOIN bookings b ON b.id=(
                   SELECT id FROM bookings WHERE ticket_id=t.id
                   ORDER BY CASE status WHEN 'booked' THEN 0 ELSE 1 END, id DESC
                   LIMIT 1)
               WHERE t.holder_phone=? AND t.status!='transferred'
               ORDER BY t.id DESC""",
            (phone,),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            ok, reason = self.zone_open(r["zone_id"], r["valid_date"])
            d["zone_open"] = ok
            d["close_reason"] = reason
            result.append(d)
        return result

    # ---------------------------------------------------------- 改期

    def reschedule(self, code: str, new_date: str, new_slot: str) -> dict:
        """改期。规则：未核销才可改（与核销互斥）；新日期可采且有名额。"""
        with db.lock():
            t = self.get_ticket(code)
            if t["status"] != "valid":
                if t["status"] in ("used", "checked_out"):
                    raise ServiceError("券已核销入园，不能改期（核销与改期不可同时进行）",
                                       "already_checked_in", 409)
                raise ServiceError(f"券当前状态为 {t['status']}，不能改期",
                                   "bad_status", 409)
            tt = self.get_ticket_type(t["type_id"])
            if new_date not in tt["valid_dates"].split(","):
                raise ServiceError("新日期不在可采日期范围内", "bad_date")
            ok, reason = self.zone_open(t["zone_id"], new_date)
            if not ok:
                raise ServiceError(f"新日期不可采摘：{reason}", "zone_closed", 409)
            if new_slot not in SLOTS:
                raise ServiceError("时段无效")
            cap = self.day_capacity(t["zone_id"], new_date)
            if cap and self.day_booked(t["zone_id"], new_date,
                                      exclude_ticket=t["id"]) >= cap:
                raise ServiceError("新日期名额已满", "sold_out", 409)
            old_bk = self.conn.execute(
                "SELECT * FROM bookings WHERE ticket_id=? AND status='booked' "
                "ORDER BY id DESC LIMIT 1", (t["id"],)
            ).fetchone()
            if old_bk:
                self.conn.execute("UPDATE bookings SET status='rescheduled' WHERE id=?",
                                  (old_bk["id"],))
            self.conn.execute(
                "INSERT INTO bookings (ticket_id,zone_id,date,slot,created_at) "
                "VALUES(?,?,?,?,?)",
                (t["id"], t["zone_id"], new_date, new_slot, now_iso()),
            )
            self.conn.execute(
                "UPDATE tickets SET valid_date=? WHERE id=?", (new_date, t["id"]))
            self.conn.commit()
            return {"ok": True, "new_date": new_date, "new_slot": new_slot}

    # ---------------------------------------------------------- 转赠

    def transfer(self, code: str, new_name: str, new_phone: str) -> dict:
        """券转赠：旧券作废（保留流水），生成新券给受赠人，预约随之转移。"""
        with db.lock():
            t = self.get_ticket(code)
            if t["status"] != "valid":
                raise ServiceError("仅未核销的券可以转赠", "bad_status", 409)
            bk = self.conn.execute(
                "SELECT * FROM bookings WHERE ticket_id=? AND status='booked' "
                "ORDER BY id DESC LIMIT 1", (t["id"],)
            ).fetchone()
            new_code = gen_code()
            cur = self.conn.execute(
                """INSERT INTO tickets
                   (code,type_id,holder_name,holder_phone,persons,children,
                    status,valid_date,zone_id,parent_ticket_id,created_at)
                   VALUES(?,?,?,?,?,?,'valid',?,?,?,?)""",
                (new_code, t["type_id"], new_name, new_phone, t["persons"],
                 t["children"], t["valid_date"], t["zone_id"], t["id"], now_iso()),
            )
            new_id = cur.lastrowid
            if bk:
                self.conn.execute(
                    "UPDATE bookings SET status='cancelled' WHERE id=?", (bk["id"],))
                self.conn.execute(
                    "INSERT INTO bookings (ticket_id,zone_id,date,slot,created_at) "
                    "VALUES(?,?,?,?,?)",
                    (new_id, bk["zone_id"], bk["date"], bk["slot"], now_iso()),
                )
            self.conn.execute("UPDATE tickets SET status='transferred' WHERE id=?",
                              (t["id"],))
            self.conn.commit()
            return {"ok": True, "new_code": new_code}

    # ---------------------------------------------------------- 入园核销

    def checkin(self, code: str, gate: str) -> dict:
        """入口核销。

        - 一张券只能核销一次（checkins.ticket_id 唯一 + 状态机 + 进程锁三重保障），
          因此不可能在两个入口重复入园；
        - 核销后状态变为 used，改期立即被拒绝，二者不可能同时成功；
        - 当日园区/分区停采（含雨天停园）时拒绝核销。
        """
        code = code.strip().upper()
        with db.lock():
            t = self.conn.execute(
                "SELECT * FROM tickets WHERE code=?", (code,)).fetchone()
            if t is None:
                raise ServiceError("券码无效", "bad_code", 404)
            if t["status"] == "transferred":
                raise ServiceError("该券已转赠，请使用新券码", "transferred", 409)
            if t["status"] in ("used", "checked_out"):
                raise ServiceError(
                    f"该券已于 {t['entry_time']} 在「{t['entry_gate']}」入口核销，"
                    f"不可重复入园", "already_checked_in", 409)
            if t["status"] == "refunded":
                raise ServiceError("该券已退款", "refunded", 409)
            if t["valid_date"] != today_str():
                raise ServiceError(
                    f"该券预约日期为 {t['valid_date']}，今天不可用（可先改期）",
                    "wrong_date", 409)
            ok, reason = self.zone_open(t["zone_id"], today_str())
            if not ok:
                raise ServiceError(f"当日停采：{reason}，请办理退改",
                                   "zone_closed", 409)
            bk = self.conn.execute(
                "SELECT * FROM bookings WHERE ticket_id=? AND status='booked' "
                "ORDER BY id DESC LIMIT 1", (t["id"],)
            ).fetchone()
            if bk is None:
                raise ServiceError("没有有效预约", "no_booking", 409)
            ts = now_iso()
            try:
                cur = self.conn.execute(
                    """INSERT INTO checkins
                       (ticket_id,booking_id,date,zone_id,gate,checked_at)
                       VALUES(?,?,?,?,?,?)""",
                    (t["id"], bk["id"], today_str(), t["zone_id"], gate, ts),
                )
            except sqlite3.IntegrityError:
                raise ServiceError("该券已核销，不可重复入园",
                                   "already_checked_in", 409)
            self.conn.execute(
                "UPDATE tickets SET status='used',entry_gate=?,entry_time=? WHERE id=?",
                (gate, ts, t["id"]),
            )
            self.conn.commit()
            return {"ok": True, "gate": gate, "checked_at": ts,
                    "checkin_id": cur.lastrowid}

    def in_park_tickets(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT t.*, z.name AS zone_name FROM tickets t
               JOIN zones z ON z.id=t.zone_id
               WHERE t.status='used' ORDER BY t.entry_time""").fetchall()

    # ---------------------------------------------------------- 称重结算

    def scale_status(self) -> dict:
        try:
            w = scale.read()
            return {"connected": True, "stable": True, "weight_g": w, "sn": scale.sn}
        except ScaleError as e:
            return {"connected": False, "stable": False, "weight_g": None,
                    "sn": scale.sn, "error": str(e)}

    def _amount_for(self, excess_g: int, cents_per_kg: int) -> int:
        # 克→千克后按单价计费，四舍五入到分
        if excess_g <= 0:
            return 0
        return (excess_g * cents_per_kg + 500) // 1000

    def weigh_and_checkout(self, code: str, operator: str = "检票员") -> dict:
        """离园称重结算。

        重量只来自电子秤读数，服务层没有任何手工写入/修改重量的入口——
        检票员角色在物理上无法修改称重结果。秤断连则中止结算，不得估重。
        """
        with db.lock():
            t = self.get_ticket(code.strip().upper())
            exists = self.conn.execute(
                "SELECT id FROM weighings WHERE ticket_id=?", (t["id"],)
            ).fetchone()
            if exists:
                raise ServiceError("该券已称重结算，结果不可修改",
                                   "already_weighed", 409)
            if t["status"] != "used":
                raise ServiceError("仅已入园（在园）的券可以称重离园",
                                   "bad_status", 409)
            tt = self.get_ticket_type(t["type_id"])
            reading = scale.read()  # 断连时抛 ScaleError
            free_g = tt["base_weight_g"] + tt["child_free_g"] * t["children"]
            free_used = min(free_g, reading)
            excess_g = max(0, reading - free_g)
            amount = self._amount_for(excess_g, tt["excess_price_cents_per_kg"])
            ts = now_iso()
            self.conn.execute(
                """INSERT INTO weighings
                   (ticket_id,gross_g,children_count,child_free_g,free_g,
                    excess_g,unit_price_cents_per_kg,amount_cents,
                    scale_sn,weighed_at,operator)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (t["id"], reading, t["children"], tt["child_free_g"], free_used,
                 excess_g, tt["excess_price_cents_per_kg"], amount,
                 scale.sn, ts, operator),
            )
            self.conn.execute(
                "UPDATE tickets SET status='checked_out',exit_time=? WHERE id=?",
                (ts, t["id"]))
            # 超额扣款：网关失败则挂账（离园后补扣），不阻止游客离园
            if amount > 0:
                pr = pay.charge(amount, "超额重量结算")
                self.conn.execute(
                    """INSERT INTO transactions
                       (ticket_id,kind,amount_cents,status,ref,note,created_at)
                       VALUES(?,?,?,?,?,?,?)""",
                    (t["id"],
                     "supplementary" if pr.status == "pending" else "excess",
                     amount, pr.status, pr.ref, pr.message, ts),
                )
                pay_state = pr.status
                pay_msg = pr.message
            else:
                pay_state = "success"
                pay_msg = "未超重，无需补款"
            self.conn.commit()
            return {"ok": True, "gross_g": reading, "free_g": free_used,
                    "excess_g": excess_g, "amount_cents": amount,
                    "pay_status": pay_state, "pay_message": pay_msg}

    def settle_supplementary(self, code: str) -> dict:
        """离园后补扣款：对挂账的超额费用重新发起扣款。"""
        with db.lock():
            t = self.get_ticket(code.strip().upper())
            w = self.conn.execute(
                "SELECT * FROM weighings WHERE ticket_id=?", (t["id"],)
            ).fetchone()
            if w is None:
                raise ServiceError("该券没有称重记录", "not_found", 404)
            paid = self.conn.execute(
                "SELECT COALESCE(SUM(amount_cents),0) AS s FROM transactions "
                "WHERE ticket_id=? AND status='success' AND kind IN ('excess','supplementary')",
                (t["id"],),
            ).fetchone()["s"]
            due = w["amount_cents"] - paid
            if due <= 0:
                return {"ok": True, "amount_cents": 0, "status": "success",
                        "message": "无待补扣金额"}
            pr = pay.charge(due, "离园后补扣款")
            self.conn.execute(
                """INSERT INTO transactions
                   (ticket_id,kind,amount_cents,status,ref,note,created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (t["id"], "supplementary", due, pr.status, pr.ref,
                 pr.message + "（离园后补扣）", now_iso()),
            )
            self.conn.commit()
            if pr.status != "success":
                raise ServiceError("补扣仍未成功：" + pr.message, "pay_pending", 402)
            return {"ok": True, "amount_cents": due, "status": "success",
                    "ref": pr.ref}

    def outstanding(self) -> list[dict]:
        rows = self.conn.execute(
            """SELECT t.id, t.code, t.holder_name, t.holder_phone,
                      w.amount_cents AS due,
                      (SELECT COALESCE(SUM(amount_cents),0) FROM transactions x
                       WHERE x.ticket_id=t.id AND x.status='success'
                         AND x.kind IN ('excess','supplementary')) AS paid
               FROM tickets t JOIN weighings w ON w.ticket_id=t.id
               WHERE t.status='checked_out'""").fetchall()
        out = []
        for r in rows:
            unpaid = r["due"] - r["paid"]
            if unpaid > 0:
                d = dict(r)
                d["unpaid_cents"] = unpaid
                out.append(d)
        return out

    # ---------------------------------------------------------- 分区管理/停采

    def set_zone_default(self, zone_id: int, status: str, reason: str | None) -> None:
        with db.lock():
            self.get_zone(zone_id)
            self.conn.execute(
                "UPDATE zones SET status=?, close_reason=? WHERE id=?",
                (status, reason, zone_id))
            self.conn.commit()

    def set_zone_day(self, zone_id: int, day: str, status: str, reason: str) -> dict:
        """设置某天某分区可采/停采（临时停采、果实未熟、雨天局部）。"""
        with db.lock():
            self.get_zone(zone_id)
            if status not in ("open", "closed"):
                raise ServiceError("状态只能是 open/closed")
            self.conn.execute(
                """INSERT INTO zone_day_status(zone_id,date,status,reason)
                   VALUES(?,?,?,?)
                   ON CONFLICT(zone_id,date) DO UPDATE SET status=excluded.status,
                     reason=excluded.reason""",
                (zone_id, day, status, reason),
            )
            self.conn.commit()
            return {"ok": True}

    def rain_close_park(self, day: str, reason: str = "雨天停园") -> dict:
        """雨天停园：当天所有分区停采。"""
        with db.lock():
            for z in self.list_crops_zones():
                self.conn.execute(
                    """INSERT INTO zone_day_status(zone_id,date,status,reason)
                       VALUES(?,?, 'closed', ?)
                       ON CONFLICT(zone_id,date) DO UPDATE SET status='closed',
                         reason=excluded.reason""",
                    (z["id"], day, reason),
                )
            self.conn.commit()
            return {"ok": True, "day": day}

    def affected_tickets(self, day: str, zone_id: int | None = None) -> list[sqlite3.Row]:
        """受停采影响、仍可退改的预约（未核销、未退款）。"""
        sql = ("SELECT t.*, tt.name AS type_name, z.name AS zone_name, b.slot "
               "FROM tickets t "
               "JOIN ticket_types tt ON tt.id=t.type_id "
               "JOIN zones z ON z.id=t.zone_id "
               "LEFT JOIN bookings b ON b.ticket_id=t.id AND b.status='booked' "
               "WHERE t.valid_date=? AND t.status='valid'")
        params: list = [day]
        if zone_id is not None:
            sql += " AND t.zone_id=?"
            params.append(zone_id)
        sql += " ORDER BY t.id"
        return self.conn.execute(sql, params).fetchall()

    def refund_ticket(self, ticket_id: int, reason: str) -> dict:
        """退券：仅未核销可退，原路退还券面价。停采受影响预约走此路径。"""
        with db.lock():
            t = self.conn.execute("SELECT * FROM tickets WHERE id=?",
                                  (ticket_id,)).fetchone()
            if t is None:
                raise ServiceError("券不存在", "not_found", 404)
            if t["status"] in ("used", "checked_out"):
                raise ServiceError("已入园的券不能退票", "bad_status", 409)
            if t["status"] == "refunded":
                raise ServiceError("券已退款", "already_refunded", 409)
            tt = self.get_ticket_type(t["type_id"])
            pr = pay.refund(tt["price_cents"], reason)
            if pr.status != "success":
                raise ServiceError("退款通道暂不可用，请稍后重试", "pay_pending", 402)
            self.conn.execute(
                "UPDATE tickets SET status='refunded' WHERE id=?", (ticket_id,))
            self.conn.execute(
                "UPDATE bookings SET status='refunded' WHERE ticket_id=? AND status='booked'",
                (ticket_id,))
            self.conn.execute(
                """INSERT INTO transactions
                   (ticket_id,kind,amount_cents,status,ref,note,created_at)
                   VALUES(?,?, 'refund',?,?,?,?)""",
                (ticket_id, tt["price_cents"], pr.status, pr.ref, reason, now_iso()),
            )
            self.conn.commit()
            return {"ok": True, "refund_cents": tt["price_cents"], "ref": pr.ref}

    def closure_options(self, day: str) -> dict:
        """临时停采时给受影响预约提供退改选项：退款或改到其他可采日期。"""
        tickets = [dict(r) for r in self.affected_tickets(day)]
        cal = self.calendar(day, days=7)
        alternatives = []
        for d, info in cal.items():
            if d <= day:
                continue
            open_zones = [z for z in info["zones"] if z["open"] and z["remaining"] > 0]
            if open_zones:
                alternatives.append({"date": d, "zones": open_zones, "slots": SLOTS})
        return {"affected": tickets, "alternatives": alternatives}

    # ---------------------------------------------------------- 流水/概览

    def transactions(self, limit: int = 100) -> list[dict]:
        rows = self.conn.execute(
            """SELECT x.*, t.code FROM transactions x
               LEFT JOIN tickets t ON t.id=x.ticket_id
               ORDER BY x.id DESC LIMIT ?""", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def overview(self) -> dict:
        def n(sql: str) -> int:
            return self.conn.execute(sql).fetchone()[0]
        return {
            "sold": n("SELECT COUNT(*) FROM tickets WHERE status!='transferred'"),
            "in_park": n("SELECT COUNT(*) FROM tickets WHERE status='used'"),
            "checked_out": n("SELECT COUNT(*) FROM tickets WHERE status='checked_out'"),
            "refunded": n("SELECT COUNT(*) FROM tickets WHERE status='refunded'"),
            "outstanding": len(self.outstanding()),
        }
