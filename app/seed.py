"""演示数据：一条命令生成完整样例，包含【次日雨天停园】等场景。

运行：python3 -m app.seed [--reset]
"""
from __future__ import annotations

import argparse
import os
from datetime import date, timedelta
from pathlib import Path

from . import db
from .adapters import pay, scale
from .services import FarmService, SLOTS, today_str

DB_PATH = Path(os.environ.get("FARM_DB",
                              str(Path(__file__).resolve().parent.parent / "farm.db")))

CROPS = [
    ("苹果", "🍎"),
    ("葡萄", "🍇"),
    ("梨", "🍐"),
    ("橘子", "🍊"),
]

# zone: (名称, 作物名)
ZONES = [
    ("南坡苹果园", "苹果"),
    ("北沟苹果园", "苹果"),
    ("藤架葡萄园", "葡萄"),
    ("香雪梨园", "梨"),
    ("暖阳橘园", "橘子"),
]

# type: (zone_idx, 名称, 价(分), 基础克重, 超额分/kg, 最多人数, 儿童免重克)
TYPES = [
    (0, "南坡苹果·亲子采摘券", 6800, 2000, 1500, 4, 500),
    (1, "北沟苹果·家庭采摘券", 9800, 3000, 1500, 5, 500),
    (2, "藤架葡萄·双人采摘券", 5800, 1500, 2600, 2, 300),
    (3, "香雪梨·家庭采摘券", 7800, 2500, 1200, 4, 500),
    (4, "暖阳橘子·亲子采摘券", 6200, 2000, 1800, 4, 500),
]


def seed(reset: bool = False) -> None:
    if reset and DB_PATH.exists():
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(DB_PATH) + suffix)
            if p.exists():
                p.unlink()

    conn = db.connect(DB_PATH)
    db.init_db(conn)
    s = FarmService(conn)

    if conn.execute("SELECT COUNT(*) c FROM ticket_types").fetchone()["c"]:
        print("数据库已有数据，跳过播种（如需重建：python3 -m app.seed --reset）")
        return

    today = date.today()
    d0, d1, d2 = (today + timedelta(days=i) for i in range(3))
    valid_dates = ",".join((today + timedelta(days=i)).isoformat() for i in range(7))

    # 作物、分区
    crop_id = {}
    for name, emoji in CROPS:
        cur = conn.execute("INSERT INTO crops(name,emoji) VALUES(?,?)", (name, emoji))
        crop_id[name] = cur.lastrowid
    zone_id = []
    for zname, cname in ZONES:
        cur = conn.execute(
            "INSERT INTO zones(name,crop_id) VALUES(?,?)", (zname, crop_id[cname]))
        zone_id.append(cur.lastrowid)

    # 券种
    type_ids = []
    for zi, name, price, base, excess_ppk, persons, child_free in TYPES:
        cur = conn.execute(
            """INSERT INTO ticket_types
               (crop_id,zone_id,name,price_cents,base_weight_g,
                excess_price_cents_per_kg,max_persons,child_free_g,valid_dates)
               VALUES((SELECT crop_id FROM zones WHERE id=?),?,?,?,?,?,?,?,?)""",
            (zone_id[zi], zone_id[zi], name, price, base, excess_ppk,
             persons, child_free, valid_dates),
        )
        type_ids.append(cur.lastrowid)

    # 名额：未来 10 天每分区每天 20 人
    for i in range(10):
        day = (today + timedelta(days=i)).isoformat()
        for zid in zone_id:
            conn.execute(
                "INSERT OR REPLACE INTO day_capacity(zone_id,date,capacity) VALUES(?,?,20)",
                (zid, day))
    conn.commit()

    # 模拟设备恢复正常
    pay.online = True
    scale.connect()

    phone = "13800000001"

    def buy(ti, day, persons=2, children=0, name="张小果", slot=SLOTS[0], ph=phone):
        return s.buy_ticket(type_ids[ti], day.isoformat(), slot, name, ph,
                            persons, children)["code"]

    codes = {}
    # ① 今天可直接核销的券（南坡苹果园）
    codes["今天待核销"] = buy(0, d0, persons=3, children=1)
    # ② 在园中的券（南坡苹果园，主入口已核销）
    codes["在园中"] = buy(0, d0, persons=2, name="李阿婆", slot=SLOTS[1],
                          ph="13800000002")
    # ③ 已离园、超额已付（葡萄园，4.2kg，免 1.5kg → 超 2.7kg）
    codes["已离园已补款"] = buy(2, d0, persons=2, name="王先生",
                                ph="13800000003")
    # ④ 已离园、网关故障挂账（梨园，离园后补扣样例）
    codes["挂账待补扣"] = buy(3, d0, persons=4, children=2, name="赵多多",
                              ph="13800000004")
    # ⑤ 明天雨天受影响的券 ×2
    codes["雨天受影响1"] = buy(0, d1, persons=4, children=1)
    codes["雨天受影响2"] = buy(2, d1, persons=2, name="陈雨", ph="13800000005")
    # ⑥ 今天暖阳橘园果实未熟，受影响券
    codes["果实未熟受影响"] = buy(4, d0, persons=3)
    # ⑦ 后天正常券
    codes["后天正常"] = buy(1, d2, persons=5)

    # 制造在园状态
    s.checkin(codes["在园中"], "东门")
    # 两笔待离园券先在各自分区入口核销
    s.checkin(codes["已离园已补款"], "西门")
    s.checkin(codes["挂账待补扣"], "主入口")

    # 制造两笔离园称重
    scale.set_weight_g(4200)
    s.weigh_and_checkout(codes["已离园已补款"], "检票员")

    pay.online = False
    scale.set_weight_g(4000)  # 免 2500+2*500=3500，超 500g
    s.weigh_and_checkout(codes["挂账待补扣"], "秤")
    pay.online = True  # 网关恢复：现场端可点「补扣款」
    scale.set_weight_g(0)

    # 天气/熟度事件
    s.set_zone_day(zone_id[4], d0.isoformat(), "closed",
                   "果实未熟，暂停开放 2 天")
    s.rain_close_park(d1.isoformat(), "气象台暴雨橙色预警，雨天停园")

    conn.commit()

    print("=" * 64)
    print("🌿 演示数据已播种：", DB_PATH)
    print(f"   今天 {d0} ｜ 明天 {d1}（🌧 雨天全园停园样例）")
    print("-" * 64)
    for k, v in codes.items():
        print(f"   {k:12s} 券码 {v}")
    print("-" * 64)
    print("   游客端 /        现场端 /gate        管理端 /admin")
    print("   推荐演示：管理端看明天雨天停园与受影响预约退改；")
    print("            现场端核销「今天待核销」券，再断开电子秤称重看报错。")
    print("=" * 64)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="删除旧库后重建")
    a = ap.parse_args()
    seed(a.reset)
