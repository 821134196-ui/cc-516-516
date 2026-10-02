"""演示数据：作物 / 园区 / 票种 / 券 / 预约 / 停采样例。

用法：
    python3 -m app.seed             # 库不存在才初始化
    python3 -m app.seed --reset     # 清库重建（演示环境推荐）

时间线（相对今天）：
    今天   正常开放，多张当日各时段券可现场核销；另有已入园/已结算/欠账样例
    后天   雨天全园停园 + 6 张受影响预约（游客端可改期/退，管理端可批量处理）
    大后天 可约
    第四天 B 区果实未熟临时停采 + 2 张受影响预约
另含未预约券、转赠券各一张。
"""
import sys
from datetime import timedelta

from .adapters import MockPayment
from .clock import Clock
from .config import DB_PATH, SLOTS
from .db import connect, init_db
from . import closures
from . import tickets as T

# zone -> (名称, 作物, 多边形点, 标签点)
ZONES = [
    ("A", "苹果园", "APP", "60,70 430,50 470,250 90,280", 250, 150),
    ("B", "梨园", "PEAR", "470,250 430,50 720,40 760,240", 590, 140),
    ("C", "葡萄园", "GRAPE", "90,280 470,250 500,470 120,500", 280, 380),
    ("D", "桃园", "PEACH", "470,250 760,240 800,480 500,470", 640, 370),
]

CROPS = [
    ("APP", "苹果", 16.0, "#c94f3d", "🍎"),
    ("PEAR", "翠冠梨", 14.0, "#8aa848", "🍐"),
    ("GRAPE", "葡萄", 22.0, "#7a5b9e", "🍇"),
    ("PEACH", "水蜜桃", 20.0, "#e8896b", "🍑"),
]

TYPES = [
    ("PICK-APP-2", "苹果双人采摘券", "APP", "A", 2, 3.0, 98),
    ("PICK-APP-1", "苹果单人采摘券", "APP", "A", 1, 2.0, 68),
    ("PICK-PEAR-2", "翠冠梨双人券", "PEAR", "B", 2, 3.5, 88),
    ("PICK-GRAPE-2", "葡萄双人采摘券", "GRAPE", "C", 2, 2.5, 128),
    ("PICK-PEACH-F", "水蜜桃亲子券", "PEACH", "D", 2, 3.0, 118),
]


def seed(reset=False):
    if reset and DB_PATH.exists():
        for suffix in ("", "-wal", "-shm"):
            p = DB_PATH.with_name(DB_PATH.name + suffix)
            if p.exists():
                p.unlink()
    init_db()
    conn = connect()
    clock = Clock()
    pay = MockPayment()

    if conn.execute("SELECT COUNT(*) c FROM crops").fetchone()["c"]:
        conn.close()
        print(f"已有数据，跳过（{DB_PATH}）。用 --reset 重建。")
        return

    for c in CROPS:
        conn.execute("INSERT INTO crops(code,name,unit_price,color,emoji) "
                     "VALUES(?,?,?,?,?)", c)
    for code, name, crop, poly, lx, ly in ZONES:
        conn.execute(
            "INSERT INTO zones(code,name,crop_code,polygon,label_x,label_y) "
            "VALUES(?,?,?,?,?,?)",
            (code, name, crop, poly, lx, ly),
        )
    for tc in TYPES:
        conn.execute(
            "INSERT INTO ticket_types(code,title,crop_code,zone_code,people,"
            "base_kg,price,valid_days) VALUES(?,?,?,?,?,?,?,14)", tc)

    today = clock.today()
    iso = lambda d: (today + timedelta(days=d)).isoformat()
    types = {r["code"]: r["id"] for r in
             conn.execute("SELECT id, code FROM ticket_types")}

    def make_ticket(code, type_code, holder, phone, children=0, gifted=None):
        tt = conn.execute("SELECT * FROM ticket_types WHERE id=?",
                          (types[type_code],)).fetchone()
        crop = conn.execute("SELECT * FROM crops WHERE code=?",
                            (tt["crop_code"],)).fetchone()
        conn.execute(
            "INSERT INTO tickets(code,type_id,crop_code,zone_code,people,children,"
            "base_kg,unit_price,amount_paid,holder_name,holder_phone,status,"
            "valid_from,valid_until,gifted_from,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,'valid',?,?,?,?)",
            (code, tt["id"], tt["crop_code"], tt["zone_code"], tt["people"],
             children, tt["base_kg"], crop["unit_price"], tt["price"],
             holder, phone, iso(0), iso(13), gifted, clock.iso()))
        tid = conn.execute("SELECT id FROM tickets WHERE code=?", (code,)).fetchone()["id"]
        conn.execute(
            "INSERT INTO payments(ticket_id,kind,amount,ref_no,note,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (tid, "ticket", tt["price"], f"MOCK-PAY-SEED{tid}", "购票", clock.iso()))
        return tid

    def book(tid, day, slot, status="booked"):
        cur = conn.execute(
            "INSERT INTO bookings(ticket_id,date,slot,status,created_at) "
            "VALUES(?,?,?,?,?)",
            (tid, iso(day), slot, status, clock.iso()))
        conn.execute("UPDATE tickets SET status=? WHERE id=?",
                     ("checked_in" if status == "checked_in" else "booked", tid))
        return cur.lastrowid

    # —— 今天四个时段各一张可核销券（演示当前时段检票）——
    today_holders = [("张磊", "13800000101"), ("李娜", "13800000102"),
                     ("王强", "13800000103"), ("赵敏", "13800000104")]
    for i, (name, phone) in enumerate(today_holders):
        tid = make_ticket(f"FARM-T{10001+i:05d}", "PICK-APP-2", name, phone)
        book(tid, 0, SLOTS[i])
    # 梨、葡萄、桃各一张今日券，供其他入口演练
    for i, (tc, name, phone) in enumerate([
            ("PICK-PEAR-2", "陈晨", "13800000201"),
            ("PICK-GRAPE-2", "林悦", "13800000202"),
            ("PICK-PEACH-F", "周桐（亲子，含1名儿童）", "13800000203")]):
        tid = make_ticket(f"FARM-S{10001+i:05d}", tc, name, phone,
                          children=1 if "亲子" in name else 0)
        book(tid, 0, SLOTS[0])

    # —— 后天：雨天全园停园，6 张受影响预约 ——
    rain_holders = [("孙浩", "13800000301", "PICK-APP-2"),
                    ("钱颖", "13800000302", "PICK-APP-1"),
                    ("吴迪", "13800000303", "PICK-PEAR-2"),
                    ("郑爽", "13800000304", "PICK-GRAPE-2"),
                    ("冯远", "13800000305", "PICK-GRAPE-2"),
                    ("褚青", "13800000306", "PICK-PEACH-F")]
    for i, (name, phone, tc) in enumerate(rain_holders):
        tid = make_ticket(f"FARM-R{10001+i:05d}", tc, name, phone)
        book(tid, 2, SLOTS[i % len(SLOTS)])
    closures.set_closure(conn, clock, iso(2), "rain",
                         "暴雨橙色预警，全天闭园，请改期或全额退款")

    # —— 第四天：B 区果实未熟临停，2 张受影响预约 ——
    for i, (name, phone) in enumerate([("卫兰", "13800000401"), ("蒋帆", "13800000402")]):
        tid = make_ticket(f"FARM-U{10001+i:05d}", "PICK-PEAR-2", name, phone)
        book(tid, 4, SLOTS[1])
    closures.set_closure(conn, clock, iso(4), "unripe",
                         "抽样糖度不达标，暂停采摘 1 天", zone_code="B")

    # —— 杂项状态券 ——
    # 未预约
    make_ticket("FARM-IDLE01", "PICK-APP-1", "何苗", "13800000501")
    # 已转赠（大后天）
    tid = make_ticket("FARM-GIFT01", "PICK-GRAPE-2", "许安", "13800000601",
                      gifted="原持有人 沈璐/13900000600")
    book(tid, 3, SLOTS[2])
    # 今日已在园内（东入口核销）
    tid = make_ticket("FARM-IN0001", "PICK-APP-2", "杨帆", "13800000701")
    bid = book(tid, 0, SLOTS[0], status="checked_in")
    conn.execute(
        "INSERT INTO entries(ticket_id,booking_id,gate,entered_at) VALUES(?,?,?,?)",
        (tid, bid, "east", clock.iso()))
    # 今日已结算离园（含超额支付成功）
    tid = make_ticket("FARM-DONE01", "PICK-APP-2", "秦朗", "13800000801")
    bid = book(tid, 0, SLOTS[0], status="checked_in")
    conn.execute(
        "INSERT INTO entries(ticket_id,booking_id,gate,entered_at,exited_at) "
        "VALUES(?,?,?,?,?)",
        (tid, bid, "east", clock.iso(), clock.iso()))
    conn.execute(
        "INSERT INTO weighings(ticket_id,entry_id,gross_kg,free_kg,charge_kg,"
        "amount,weighed_at,locked) VALUES(?,?,4.6,3.0,1.6,25.6,?,1)",
        (tid, conn.execute("SELECT id FROM entries WHERE ticket_id=?", (tid,))
         .fetchone()["id"], clock.iso()))
    conn.execute(
        "INSERT INTO payments(ticket_id,kind,amount,ref_no,note,created_at) "
        "VALUES(?,?,?,'MOCK-PAY-DONE','超额重量结算',?)",
        (tid, "extra", 25.6, clock.iso()))
    conn.execute("UPDATE tickets SET status='weighed' WHERE id=?", (tid,))

    # 今日已离园但超额扣款失败 -> 欠账（管理端演示离园后补扣）
    tid = make_ticket("FARM-DEBT01", "PICK-PEAR-2", "许诺", "13800000901")
    bid = book(tid, 0, SLOTS[0], status="checked_in")
    conn.execute(
        "INSERT INTO entries(ticket_id,booking_id,gate,entered_at,exited_at) "
        "VALUES(?,?,?,?,?)",
        (tid, bid, "west", clock.iso(), clock.iso()))
    eid = conn.execute("SELECT id FROM entries WHERE ticket_id=?", (tid,))
    eid = eid.fetchone()["id"]
    conn.execute(
        "INSERT INTO weighings(ticket_id,entry_id,gross_kg,free_kg,charge_kg,"
        "amount,weighed_at,locked) VALUES(?,?,6.0,3.5,2.5,35.0,?,1)",
        (tid, eid, clock.iso()))
    conn.execute(
        "INSERT INTO payments(ticket_id,kind,amount,status,ref_no,note,created_at) "
        "VALUES(?,?,?,'failed','MOCK-FAIL-DEBT','现场支付失败，离园后补扣',?)",
        (tid, "extra", 35.0, clock.iso()))
    conn.execute(
        "INSERT INTO debts(ticket_id,amount,reason,created_at) VALUES(?,?,?,?)",
        (tid, 35.0, "现场支付失败，离园后补扣", clock.iso()))
    conn.execute("UPDATE tickets SET status='weighed' WHERE id=?", (tid,))

    conn.close()
    print(f"演示数据就绪：{DB_PATH}")
    print("  今天：FARM-T10001~T10004（分时段）/ FARM-S10001~S10003 可核销")
    print("  雨天停园：FARM-R10001~R10006（后天）")
    print("  未熟临停：FARM-U10001~U10002（第四天，B 区）")
    print("  其他：FARM-IDLE01 未预约 / FARM-GIFT01 已转赠 / "
          "FARM-IN0001 在园 / FARM-DONE01 已结算 / FARM-DEBT01 待补扣")


if __name__ == "__main__":
    seed(reset="--reset" in sys.argv)
