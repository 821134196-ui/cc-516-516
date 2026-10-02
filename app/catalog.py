"""目录与采摘日历查询。"""
from datetime import timedelta

from .config import SLOTS, SLOT_CAPACITY
from .db import tx


ACTIVE_BOOKING = "b.status IN ('booked','checked_in')"


def list_crops(conn):
    return conn.execute("SELECT * FROM crops ORDER BY code").fetchall()


def list_zones(conn):
    return conn.execute(
        "SELECT z.*, c.name crop_name, c.emoji, c.color, c.unit_price "
        "FROM zones z JOIN crops c ON c.code = z.crop_code ORDER BY z.code"
    ).fetchall()


def list_ticket_types(conn):
    return conn.execute(
        "SELECT tt.*, c.name crop_name, c.emoji, z.name zone_name "
        "FROM ticket_types tt JOIN crops c ON c.code=tt.crop_code "
        "JOIN zones z ON z.code=tt.zone_code ORDER BY tt.price"
    ).fetchall()


def closure_map(conn, date):
    """返回 {'__park__': row|None, 'A': row, ...}"""
    rows = conn.execute("SELECT * FROM zone_status WHERE date=?", (date,)).fetchall()
    m = {}
    for r in rows:
        m[r["zone_code"] or "__park__"] = r
    return m


def park_closed(conn, date):
    row = conn.execute(
        "SELECT * FROM zone_status WHERE date=? AND zone_code IS NULL", (date,)
    ).fetchone()
    return row


def zone_closed(conn, zone_code, date):
    row = park_closed(conn, date)
    if row:
        return row
    return conn.execute(
        "SELECT * FROM zone_status WHERE date=? AND zone_code=?",
        (date, zone_code),
    ).fetchone()


def slot_used(conn, zone_code, date, slot):
    return conn.execute(
        f"SELECT COUNT(*) c FROM bookings b JOIN tickets t ON t.id=b.ticket_id "
        f"WHERE b.date=? AND b.slot=? AND t.zone_code=? AND {ACTIVE_BOOKING}",
        (date, slot, zone_code),
    ).fetchone()["c"]


def calendar(conn, clock, days=14):
    """游客端日历：日期 × 园区 × 时段 的可采状态与剩余名额。"""
    today = clock.today()
    zones = list_zones(conn)
    out = []
    for d in range(days):
        day = today + timedelta(days=d)
        diso = day.isoformat()
        cm = closure_map(conn, diso)
        park = cm.get("__park__")
        zcards = []
        pickable_any = False
        for z in zones:
            zrow = cm.get(z["code"])
            closed = park or zrow
            slots_info = []
            for s in SLOTS:
                used = slot_used(conn, z["code"], diso, s)
                left = max(0, SLOT_CAPACITY - used)
                slots_info.append({
                    "slot": s, "used": used, "left": left,
                    "full": left <= 0,
                })
            if not closed:
                pickable_any = True
            zcards.append({
                "zone": z, "closure": zrow,
                "open": closed is None, "slots": slots_info,
            })
        out.append({
            "date": diso,
            "weekday": "一二三四五六日"[day.weekday()],
            "park_closure": park,
            "pickable": pickable_any and park is None,
            "zones": zcards,
        })
    return out
