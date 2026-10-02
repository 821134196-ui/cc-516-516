"""分区状态：当天停采设置、雨天全园停园、受影响预约与退改。"""
from . import catalog
from .db import tx
from .errors import FarmError
from .tickets import refund_ticket, reschedule

REASONS = {
    "rain": "雨天停园",
    "unripe": "果实未熟",
    "other": "临时管护",
}


def set_closure(conn, clock, date, reason, note="", zone_code=None, actor="manager"):
    """设置停采。zone_code=None 表示全园（雨天停园）。"""
    if reason not in REASONS:
        raise FarmError("停采原因无效")
    with tx(conn):
        conn.execute(
            "INSERT INTO zone_status(zone_code,date,reason,note,set_at,set_by) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(zone_code,date) DO UPDATE SET "
            "reason=excluded.reason,note=excluded.note,set_at=excluded.set_at,"
            "set_by=excluded.set_by",
            (zone_code, date, reason, note, clock.iso(), actor),
        )
        conn.execute(
            "INSERT INTO ledger(event,detail,actor,created_at) VALUES(?,?,?,?)",
            ("closure", f"停采 {zone_code or '全园'} {date} {REASONS[reason]} {note}",
             actor, clock.iso()),
        )


def remove_closure(conn, clock, date, zone_code=None, actor="manager"):
    with tx(conn):
        cur = conn.execute(
            "DELETE FROM zone_status WHERE date=? AND "
            + ("zone_code IS NULL" if zone_code is None else "zone_code=?"),
            (date,) if zone_code is None else (date, zone_code),
        )
        if cur.rowcount == 0:
            raise FarmError("该停采记录不存在")
        conn.execute(
            "INSERT INTO ledger(event,detail,actor,created_at) VALUES(?,?,?,?)",
            ("closure_remove", f"解除停采 {zone_code or '全园'} {date}",
             actor, clock.iso()),
        )


def affected_bookings(conn, date, zone_code=None):
    """受某次停采影响的有效预约：指定区 or 全园。"""
    sql = ("SELECT b.*, t.code tcode, t.zone_code tzone, t.holder_name, t.holder_phone, t.people, "
           "t.children, z.name zone_name, c.emoji FROM bookings b "
           "JOIN tickets t ON t.id=b.ticket_id "
           "JOIN zones z ON z.code=t.zone_code "
           "JOIN crops c ON c.code=t.crop_code "
           "WHERE b.date=? AND b.status='booked'")
    params = [date]
    if zone_code:
        sql += " AND t.zone_code=?"
        params.append(zone_code)
    sql += " ORDER BY b.slot, t.code"
    return conn.execute(sql, params).fetchall()


def offer_reschedule(conn, clock, booking_id, new_date, new_slot, actor="manager"):
    """对单条受影响预约执行改期（目标区仍按券所属区校验名额）。"""
    row = conn.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
    t = conn.execute("SELECT code FROM tickets WHERE id=?",
                     (row["ticket_id"],)).fetchone()
    return reschedule(conn, clock, t["code"], new_date, new_slot,
                      reason="closure", actor=actor)


def offer_refund(conn, clock, booking_id, payment, actor="manager"):
    """对单条受影响预约执行全额退款。"""
    row = conn.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
    t = conn.execute("SELECT * FROM tickets WHERE id=?",
                     (row["ticket_id"],)).fetchone()
    refund_ticket(conn, clock, t["code"], t["amount_paid"],
                  "停采全额退", actor=actor, payment=payment)
