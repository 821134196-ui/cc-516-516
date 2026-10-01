"""领域服务测试：券重放、重量计费、停采改期、并发入园等关键规则。"""
from __future__ import annotations

import inspect
import sqlite3
import tempfile
import threading
import unittest
from datetime import date, timedelta
from pathlib import Path

from app import db
from app.adapters import pay, scale, ScaleError
from app.services import FarmService, ServiceError, SLOTS, today_str


class FarmTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.conn = db.connect(self.db_path)
        db.init_db(self.conn)
        self.s = FarmService(self.conn)
        # 模拟设备复位
        pay.online = True
        scale.connect()
        scale.set_weight_g(0)
        self._seed_catalog()

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def _seed_catalog(self) -> None:
        today = date.today()
        self.dates = [(today + timedelta(days=i)).isoformat() for i in range(7)]
        cid = self.conn.execute(
            "INSERT INTO crops(name,emoji) VALUES('苹果','🍎')").lastrowid
        self.zid = self.conn.execute(
            "INSERT INTO zones(name,crop_id) VALUES('测试苹果园',?)", (cid,)).lastrowid
        self.tid = self.conn.execute(
            """INSERT INTO ticket_types
               (crop_id,zone_id,name,price_cents,base_weight_g,
                excess_price_cents_per_kg,max_persons,child_free_g,valid_dates)
               VALUES(?,?, '测试券',6800,2000,1500,4,500,?)""",
            (cid, self.zid, ",".join(self.dates))).lastrowid
        for d in self.dates:
            self.conn.execute(
                "INSERT OR REPLACE INTO day_capacity(zone_id,date,capacity) VALUES(?,?,5)",
                (self.zid, d))
        self.conn.commit()

    def buy(self, day: str | None = None, children: int = 0,
            persons: int = 2, phone: str = "13900000000") -> str:
        return self.s.buy_ticket(
            self.tid, day or self.dates[0], SLOTS[0],
            "测试游客", phone, persons, children)["code"]


class TestTicketReplay(FarmTestBase):
    """券重放：一张券不能重复核销、不能跨入口重复入园。"""

    def test_checkin_once(self) -> None:
        code = self.buy()
        self.assertTrue(self.s.checkin(code, "主入口")["ok"])
        with self.assertRaises(ServiceError) as cm:
            self.s.checkin(code, "东门")
        self.assertEqual(cm.exception.code, "already_checked_in")

    def test_replay_at_other_gate(self) -> None:
        code = self.buy()
        self.s.checkin(code, "主入口")
        for gate in ("东门", "西门", "主入口"):
            with self.assertRaises(ServiceError):
                self.s.checkin(code, gate)
        t = self.s.get_ticket(code)
        self.assertEqual(t["status"], "used")
        self.assertEqual(t["entry_gate"], "主入口")

    def test_checkin_and_reschedule_mutually_exclusive(self) -> None:
        """核销与改期不能同时成功（顺序路径）。"""
        code = self.buy()
        self.s.checkin(code, "主入口")
        with self.assertRaises(ServiceError) as cm:
            self.s.reschedule(code, self.dates[2], SLOTS[1])
        self.assertEqual(cm.exception.code, "already_checked_in")

    def test_wrong_date_checkin(self) -> None:
        code = self.buy(day=self.dates[2])
        with self.assertRaises(ServiceError) as cm:
            self.s.checkin(code, "主入口")
        self.assertEqual(cm.exception.code, "wrong_date")

    def test_concurrent_checkin_only_one_succeeds(self) -> None:
        """20 个线程在不同入口并发核销同一张券，恰好 1 次成功。"""
        code = self.buy()
        results: list[bool] = []
        barrier = threading.Barrier(20)

        def worker(i: int) -> None:
            barrier.wait()
            try:
                self.s.checkin(code, f"入口{i}")
                results.append(True)
            except ServiceError:
                results.append(False)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sum(results), 1)

    def test_concurrent_checkin_vs_reschedule(self) -> None:
        """并发核销与改期：不允许两者都成功。"""
        code = self.buy()
        outcome = {}
        barrier = threading.Barrier(2)

        def do_checkin() -> None:
            barrier.wait()
            try:
                self.s.checkin(code, "主入口")
                outcome["checkin"] = True
            except ServiceError:
                outcome["checkin"] = False

        def do_reschedule() -> None:
            barrier.wait()
            try:
                self.s.reschedule(code, self.dates[1], SLOTS[1])
                outcome["reschedule"] = True
            except ServiceError:
                outcome["reschedule"] = False

        t1 = threading.Thread(target=do_checkin)
        t2 = threading.Thread(target=do_reschedule)
        t1.start(); t2.start(); t1.join(); t2.join()
        self.assertFalse(outcome["checkin"] and outcome["reschedule"],
                         "核销与改期同时成功了")
        t = self.s.get_ticket(code)
        self.assertIn(t["status"], ("valid", "used"))
        if t["status"] == "valid":
            self.assertEqual(t["valid_date"], self.dates[1])


class TestWeightBilling(FarmTestBase):
    """重量计费：基础重量、儿童免费重量、超额单价、结果不可改。"""

    def test_within_free_weight(self) -> None:
        code = self.buy(children=1)  # 免 2000 + 500 = 2500g
        self.s.checkin(code, "主入口")
        scale.set_weight_g(2500)
        r = self.s.weigh_and_checkout(code)
        self.assertEqual(r["excess_g"], 0)
        self.assertEqual(r["amount_cents"], 0)

    def test_excess_billing_with_child_free(self) -> None:
        code = self.buy(children=1)  # 免 2500g
        self.s.checkin(code, "主入口")
        scale.set_weight_g(4200)    # 超 1700g
        r = self.s.weigh_and_checkout(code)
        self.assertEqual(r["gross_g"], 4200)
        self.assertEqual(r["free_g"], 2500)
        self.assertEqual(r["excess_g"], 1700)
        # 1700g × 1500 分/kg = 2550 分
        self.assertEqual(r["amount_cents"], 2550)

    def test_no_child_no_bonus(self) -> None:
        code = self.buy(children=0)  # 免 2000g
        self.s.checkin(code, "主入口")
        scale.set_weight_g(2500)    # 超 500g
        r = self.s.weigh_and_checkout(code)
        self.assertEqual(r["excess_g"], 500)
        self.assertEqual(r["amount_cents"], 750)

    def test_weighing_immutable(self) -> None:
        code = self.buy()
        self.s.checkin(code, "主入口")
        scale.set_weight_g(3000)
        self.s.weigh_and_checkout(code)
        scale.set_weight_g(5000)  # 试图改读数
        with self.assertRaises(ServiceError) as cm:
            self.s.weigh_and_checkout(code)
        self.assertEqual(cm.exception.code, "already_weighed")
        w = self.conn.execute(
            "SELECT * FROM weighings WHERE ticket_id=("
            "SELECT id FROM tickets WHERE code=?)", (code,)).fetchone()
        self.assertEqual(w["gross_g"], 3000)

    def test_inspector_cannot_supply_weight(self) -> None:
        """结构性约束：称重接口只接收券码与操作人，没有任何重量入参。"""
        params = inspect.signature(self.s.weigh_and_checkout).parameters
        self.assertEqual(set(params), {"code", "operator"})

    def test_scale_disconnected_blocks_checkout(self) -> None:
        """电子秤断连：禁止结算（不能人工估重），券仍在园。"""
        code = self.buy()
        self.s.checkin(code, "主入口")
        scale.disconnect()
        with self.assertRaises(ScaleError):
            self.s.weigh_and_checkout(code)
        t = self.s.get_ticket(code)
        self.assertEqual(t["status"], "used")
        n = self.conn.execute(
            "SELECT COUNT(*) c FROM weighings w JOIN tickets t ON t.id=w.ticket_id "
            "WHERE t.code=?", (code,)).fetchone()["c"]
        self.assertEqual(n, 0)
        # 恢复后可正常结算
        scale.connect()
        scale.set_weight_g(2100)
        r = self.s.weigh_and_checkout(code)
        self.assertEqual(r["excess_g"], 100)

    def test_supplementary_payment_after_exit(self) -> None:
        """网关故障离园挂账，恢复后离园补扣款。"""
        code = self.buy()
        self.s.checkin(code, "主入口")
        scale.set_weight_g(3000)  # 超 1000g → 1500 分
        pay.online = False
        r = self.s.weigh_and_checkout(code)
        self.assertEqual(r["pay_status"], "pending")
        self.assertEqual(r["amount_cents"], 1500)
        # 出现在待补扣列表
        outstanding = self.s.outstanding()
        self.assertEqual([o["code"] for o in outstanding], [code])
        # 网关未恢复，补扣失败但不重复入账成功金额
        with self.assertRaises(ServiceError):
            self.s.settle_supplementary(code)
        # 网关恢复，补扣成功
        pay.online = True
        r2 = self.s.settle_supplementary(code)
        self.assertEqual(r2["amount_cents"], 1500)
        self.assertEqual(self.s.outstanding(), [])
        # 再次补扣为 0
        self.assertEqual(self.s.settle_supplementary(code)["amount_cents"], 0)


class TestClosureAndReschedule(FarmTestBase):
    """雨天停园、果实未熟、临时停采与退改。"""

    def test_rain_blocks_calendar_and_checkin(self) -> None:
        day = self.dates[0]
        code = self.buy(day=day)  # 雨前已购券
        self.s.rain_close_park(day, "雨天停园")
        ok, reason = self.s.zone_open(self.zid, day)
        self.assertFalse(ok)
        self.assertIn("雨", reason)
        # 已存在的今天预约无法核销
        with self.assertRaises(ServiceError) as cm:
            self.s.checkin(code, "主入口")
        self.assertEqual(cm.exception.code, "zone_closed")
        # 雨天不能买当天券
        with self.assertRaises(ServiceError):
            self.buy(day=day)

    def test_unripe_zone_day(self) -> None:
        self.s.set_zone_day(self.zid, self.dates[1], "closed", "果实未熟")
        ok, reason = self.s.zone_open(self.zid, self.dates[1])
        self.assertFalse(ok)
        self.assertEqual(reason, "果实未熟")

    def test_closure_refund_and_reschedule_options(self) -> None:
        c1 = self.buy(day=self.dates[1])
        c2 = self.buy(day=self.dates[1])
        self.s.set_zone_day(self.zid, self.dates[1], "closed", "临时养护停采")

        affected = self.s.affected_tickets(self.dates[1])
        self.assertEqual({r["code"] for r in affected}, {c1, c2})

        # 券一：退款（原路退回券面价）
        t1 = self.s.get_ticket(c1)
        r = self.s.refund_ticket(t1["id"], "停采退票")
        self.assertEqual(r["refund_cents"], 6800)
        self.assertEqual(self.s.get_ticket(c1)["status"], "refunded")

        # 券二：免费改期到可采日
        r2 = self.s.reschedule(c2, self.dates[3], SLOTS[2])
        self.assertEqual(r2["new_date"], self.dates[3])
        self.assertEqual(self.s.get_ticket(c2)["valid_date"], self.dates[3])
        # 原预约标记 rescheduled，新预约 booked
        rows = self.conn.execute(
            "SELECT b.status FROM bookings b JOIN tickets t ON t.id=b.ticket_id "
            "WHERE t.code=? ORDER BY b.id", (c2,)).fetchall()
        self.assertEqual([r["status"] for r in rows], ["rescheduled", "booked"])
        # 停采日不再有受影响预约
        self.assertEqual(
            {r["code"] for r in self.s.affected_tickets(self.dates[1])}, set())

    def test_closure_options_include_alternatives(self) -> None:
        self.buy(day=self.dates[0])
        self.s.rain_close_park(self.dates[0], "雨天停园")
        opts = self.s.closure_options(self.dates[0])
        self.assertEqual(len(opts["affected"]), 1)
        alt_dates = [a["date"] for a in opts["alternatives"]]
        self.assertIn(self.dates[1], alt_dates)

    def test_refund_releases_capacity(self) -> None:
        # 把今天名额调成 1
        self.conn.execute(
            "UPDATE day_capacity SET capacity=1 WHERE zone_id=? AND date=?",
            (self.zid, self.dates[0]))
        self.conn.commit()
        code = self.buy(day=self.dates[0])
        with self.assertRaises(ServiceError) as cm:
            self.buy(day=self.dates[0], phone="13900000002")
        self.assertEqual(cm.exception.code, "sold_out")
        tid = self.s.get_ticket(code)["id"]
        self.s.refund_ticket(tid, "计划变更")
        # 名额释放后可再购
        code2 = self.buy(day=self.dates[0], phone="13900000002")
        self.assertTrue(code2)

    def test_checked_in_ticket_not_refundable(self) -> None:
        code = self.buy()
        self.s.checkin(code, "主入口")
        with self.assertRaises(ServiceError) as cm:
            self.s.refund_ticket(self.s.get_ticket(code)["id"], "x")
        self.assertEqual(cm.exception.code, "bad_status")


class TestTransfer(FarmTestBase):
    def test_transfer_voids_old_code(self) -> None:
        code = self.buy()
        r = self.s.transfer(code, "受赠人", "13700000000")
        new_code = r["new_code"]
        self.assertNotEqual(code, new_code)
        # 旧码核销报“已转赠”
        with self.assertRaises(ServiceError) as cm:
            self.s.checkin(code, "主入口")
        self.assertEqual(cm.exception.code, "transferred")
        # 新码可正常核销
        self.assertTrue(self.s.checkin(new_code, "主入口")["ok"])
        # 受赠人能查到，原持有人查不到
        self.assertEqual(
            [t["code"] for t in self.s.my_tickets("13700000000")], [new_code])
        self.assertEqual(self.s.my_tickets("13900000000"), [])

    def test_transfer_after_checkin_rejected(self) -> None:
        code = self.buy()
        self.s.checkin(code, "主入口")
        with self.assertRaises(ServiceError):
            self.s.transfer(code, "x", "13700000001")


class TestCalendar(FarmTestBase):
    def test_calendar_shows_remaining(self) -> None:
        self.buy()
        self.buy(phone="13900000002")
        cal = self.s.calendar(today_str(), days=3)
        z0 = cal[self.dates[0]]["zones"][0]
        self.assertEqual(z0["capacity"], 5)
        self.assertEqual(z0["booked"], 2)
        self.assertEqual(z0["remaining"], 3)
        self.s.rain_close_park(self.dates[0], "雨")
        cal = self.s.calendar(today_str(), days=1)
        self.assertFalse(cal[self.dates[0]]["park_open"])
        self.assertEqual(cal[self.dates[0]]["zones"][0]["remaining"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
