"""系统测试：券重放 / 重量计费 / 停采改期 / 并发入园（+ 欠账补扣、转赠、断连）。

运行：python3 -m unittest discover -s tests -v
"""
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path

from app import closures as C
from app import operations as O
from app import tickets as T
from app.adapters import MockPayment, MockScale, ScaleError
from app.clock import Clock
from app.db import connect, init_db
from app.errors import TicketStateError


def fixed_clock(day=2, hour=10, minute=0):
    return Clock(fixed=datetime(2026, 10, day, hour, minute, 0))


class FarmTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "test.db")
        init_db(self.db_path)
        self.conn = connect(self.db_path)
        self.pay = MockPayment()
        self.scale = MockScale()
        self.clock = fixed_clock()
        self._seed_catalog()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _seed_catalog(self):
        c = self.conn
        c.execute("INSERT INTO crops(code,name,unit_price,color,emoji) "
                  "VALUES('APP','苹果',16.0,'#c94f3d','🍎')")
        c.execute("INSERT INTO zones(code,name,crop_code,polygon,label_x,label_y) "
                  "VALUES('A','苹果园','APP','0,0 10,0 10,10 0,10',5,5)")
        c.execute("INSERT INTO ticket_types(code,title,crop_code,zone_code,people,"
                  "base_kg,price,valid_days) VALUES('T2','双人券','APP','A',2,3.0,98,14)")
        self.type_id = c.execute(
            "SELECT id FROM ticket_types WHERE code='T2'").fetchone()["id"]

    def buy(self, children=0):
        t = T.purchase(self.conn, self.clock, self.type_id,
                       "游客甲", "13800000000", children=children, payment=self.pay)
        return t["code"]

    def book_today(self, code, slot="08:30-11:00"):
        return T.book(self.conn, self.clock, code, "2026-10-02", slot)


class TestTicketReplay(FarmTestBase):
    """券重放：一张券只能核销一次，第二入口/重复扫码必须拒绝。"""

    def test_redeem_twice_same_gate_rejected(self):
        code = self.buy()
        self.book_today(code)
        O.redeem(self.conn, self.clock, code, "east")
        with self.assertRaises(O.ReplayError) as cm:
            O.redeem(self.conn, self.clock, code, "east")
        self.assertIn("不能重复核销", str(cm.exception))
        # 只产生一条入园记录
        n = self.conn.execute(
            "SELECT COUNT(*) c FROM entries e JOIN tickets t ON t.id=e.ticket_id "
            "WHERE t.code=?", (code,)).fetchone()["c"]
        self.assertEqual(n, 1)

    def test_replay_at_other_gate_reports_first_entry(self):
        code = self.buy()
        self.book_today(code)
        O.redeem(self.conn, self.clock, code, "east")
        with self.assertRaises(O.ReplayError) as cm:
            O.redeem(self.conn, self.clock, code, "west")
        self.assertEqual(cm.exception.info["gate"], "east")
        self.assertTrue(cm.exception.info["entered_at"])

    def test_weigh_result_locked_and_staff_cannot_edit(self):
        code = self.buy()
        self.book_today(code)
        O.redeem(self.conn, self.clock, code, "east")
        self.scale.set_reading(5.0)
        r1 = O.weigh_and_checkout(self.conn, self.clock, code, self.scale, self.pay)
        self.assertEqual(r1["amount"], 32.0)  # (5-3)*16
        # 检票员再称/改数：拒绝，结果锁定
        self.scale.set_reading(0.5)
        with self.assertRaises(TicketStateError):
            O.weigh_and_checkout(self.conn, self.clock, code, self.scale, self.pay)
        w = self.conn.execute(
            "SELECT w.* FROM weighings w JOIN tickets t ON t.id=w.ticket_id "
            "WHERE t.code=?", (code,)).fetchone()
        self.assertEqual(w["gross_kg"], 5.0)
        self.assertEqual(w["locked"], 1)

    def test_unbooked_ticket_cannot_redeem(self):
        code = self.buy()
        with self.assertRaises(TicketStateError):
            O.redeem(self.conn, self.clock, code, "east")


class TestWeighingBilling(FarmTestBase):
    """重量计费：基础重量 + 儿童免费重量，按作物单价结算。"""

    def test_within_free_allowance_no_charge(self):
        code = self.buy(children=1)          # 3.0 + 0.5 = 3.5kg 免费
        self.book_today(code)
        O.redeem(self.conn, self.clock, code, "east")
        self.scale.set_reading(3.5)
        r = O.weigh_and_checkout(self.conn, self.clock, code, self.scale, self.pay)
        self.assertEqual((r["free_kg"], r["charge_kg"], r["amount"]), (3.5, 0.0, 0.0))

    def test_child_free_weight_deducted_before_billing(self):
        code = self.buy(children=2)          # 3.0 + 1.0 = 4.0kg 免费
        self.book_today(code)
        O.redeem(self.conn, self.clock, code, "east")
        self.scale.set_reading(5.0)
        r = O.weigh_and_checkout(self.conn, self.clock, code, self.scale, self.pay)
        self.assertEqual(r["free_kg"], 4.0)
        self.assertEqual(r["charge_kg"], 1.0)
        self.assertEqual(r["amount"], 16.0)

    def test_payment_failure_becomes_debt_and_collected_after_exit(self):
        code = self.buy()
        self.book_today(code)
        O.redeem(self.conn, self.clock, code, "east")
        self.scale.set_reading(6.0)          # 3kg * 16 = 48
        self.pay.fail_next = True            # 现场扣款失败：不拦离场
        r = O.weigh_and_checkout(self.conn, self.clock, code, self.scale, self.pay)
        self.assertEqual(r["amount"], 48.0)
        self.assertIsNotNone(r["debt"])
        tid = self.conn.execute("SELECT id FROM tickets WHERE code=?",
                                (code,)).fetchone()["id"]
        debt = self.conn.execute(
            "SELECT * FROM debts WHERE ticket_id=?", (tid,)).fetchone()
        self.assertEqual(debt["status"], "open")
        # 离园后补扣
        ref = O.settle_debt(self.conn, self.clock, debt["id"], self.pay)
        self.assertTrue(ref.startswith("MOCK-PAY"))
        debt2 = self.conn.execute("SELECT * FROM debts WHERE id=?",
                                  (debt["id"],)).fetchone()
        self.assertEqual(debt2["status"], "collected")
        kinds = [r["kind"] for r in self.conn.execute(
            "SELECT kind FROM payments WHERE ticket_id=? ORDER BY id", (tid,))]
        self.assertEqual(kinds, ["ticket", "extra", "arrears"])

    def test_scale_disconnected_blocks_weighing_without_record(self):
        code = self.buy()
        self.book_today(code)
        O.redeem(self.conn, self.clock, code, "east")
        self.scale.disconnect()
        with self.assertRaises(ScaleError):
            O.weigh_and_checkout(self.conn, self.clock, code, self.scale, self.pay)
        # 断连期间不得产生称重/离场记录，券仍在园内，恢复后可正常结算
        tid = self.conn.execute("SELECT id FROM tickets WHERE code=?",
                                (code,)).fetchone()["id"]
        self.assertIsNone(self.conn.execute(
            "SELECT 1 FROM weighings WHERE ticket_id=?", (tid,)).fetchone())
        t = self.conn.execute("SELECT status FROM tickets WHERE id=?",
                              (tid,)).fetchone()
        self.assertEqual(t["status"], "redeemed")
        self.scale.connect()
        self.scale.set_reading(4.0)
        r = O.weigh_and_checkout(self.conn, self.clock, code, self.scale, self.pay)
        self.assertEqual(r["amount"], 16.0)


class TestClosureReschedule(FarmTestBase):
    """雨天停园 / 临停：核销拦截、受影响预约退改。"""

    def test_rain_closure_blocks_redeem_and_lists_affected(self):
        code = self.buy()
        self.book_today(code)
        C.set_closure(self.conn, self.clock, "2026-10-02", "rain", "暴雨闭园")
        with self.assertRaises(TicketStateError) as cm:
            O.redeem(self.conn, self.clock, code, "east")
        self.assertIn("停采", str(cm.exception))
        affected = C.affected_bookings(self.conn, "2026-10-02")
        self.assertEqual([b["tcode"] for b in affected], [code])

    def test_reschedule_to_open_day_then_redeem_succeeds(self):
        code = self.buy()
        self.book_today(code)
        C.set_closure(self.conn, self.clock, "2026-10-02", "rain", "暴雨闭园")
        # 当天改期到次日开放日
        T.reschedule(self.conn, self.clock, code, "2026-10-03", "08:30-11:00",
                     reason="closure")
        # 今日核销仍应失败（预约日期不匹配）
        with self.assertRaises(TicketStateError):
            O.redeem(self.conn, self.clock, code, "east")
        # 次日核销成功
        tomorrow = fixed_clock(day=3)
        O.redeem(self.conn, tomorrow, code, "east")
        t = self.conn.execute("SELECT status FROM tickets WHERE code=?",
                              (code,)).fetchone()
        self.assertEqual(t["status"], "redeemed")
        # 核销后再改期必须失败
        with self.assertRaises(TicketStateError):
            T.reschedule(self.conn, tomorrow, code, "2026-10-04", "08:30-11:00")

    def test_zone_closure_only_affects_that_zone(self):
        code = self.buy()
        self.book_today(code)
        # B 区停采不影响 A 区券
        self.conn.execute(
            "INSERT INTO zones(code,name,crop_code,polygon,label_x,label_y) "
            "VALUES('B','梨园','APP','0,0 1,1 1,0',1,1)")
        C.set_closure(self.conn, self.clock, "2026-10-02", "unripe",
                      "糖度不足", zone_code="B")
        O.redeem(self.conn, self.clock, code, "east")   # 不抛异常即可

    def test_full_refund_for_affected_booking(self):
        code = self.buy()
        self.book_today(code)
        C.set_closure(self.conn, self.clock, "2026-10-02", "unripe", "未熟")
        T.refund_ticket(self.conn, self.clock, code, 98.0, "停采全额退",
                        payment=self.pay)
        t = self.conn.execute("SELECT * FROM tickets WHERE code=?",
                              (code,)).fetchone()
        self.assertEqual(t["status"], "refunded")
        refund = self.conn.execute(
            "SELECT * FROM payments WHERE ticket_id=? AND kind='refund'",
            (t["id"],)).fetchone()
        self.assertEqual(refund["amount"], -98.0)
        # 已退款券不能再核销
        with self.assertRaises(TicketStateError):
            O.redeem(self.conn, self.clock, code, "east")

    def test_remove_closure_restores_redeem(self):
        code = self.buy()
        self.book_today(code)
        C.set_closure(self.conn, self.clock, "2026-10-02", "unripe", "未熟",
                      zone_code="A")
        with self.assertRaises(TicketStateError):
            O.redeem(self.conn, self.clock, code, "east")
        C.remove_closure(self.conn, self.clock, "2026-10-02", zone_code="A")
        O.redeem(self.conn, self.clock, code, "east")


class TestConcurrentEntry(unittest.TestCase):
    """并发入园：两入口同时扫同一张券，只能有一次成功。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "test.db")
        init_db(self.db_path)
        admin = connect(self.db_path)
        admin.execute("INSERT INTO crops(code,name,unit_price,color,emoji) "
                      "VALUES('APP','苹果',16.0,'#c94f3d','🍎')")
        admin.execute("INSERT INTO zones(code,name,crop_code,polygon,label_x,label_y) "
                      "VALUES('A','苹果园','APP','0,0 10,0 10,10 0,10',5,5)")
        admin.execute("INSERT INTO ticket_types(code,title,crop_code,zone_code,"
                      "people,base_kg,price,valid_days) "
                      "VALUES('T2','双人券','APP','A',2,3.0,98,14)")
        type_id = admin.execute("SELECT id FROM ticket_types").fetchone()["id"]
        clock = fixed_clock()
        t = T.purchase(admin, clock, type_id, "并发游客", "138", payment=MockPayment())
        T.book(admin, clock, t["code"], "2026-10-02", "08:30-11:00")
        admin.close()
        self.code = t["code"]

    def tearDown(self):
        self.tmp.cleanup()

    def test_two_gates_simultaneous_only_one_enters(self):
        results = []
        barrier = threading.Barrier(2)

        def worker(gate):
            conn = connect(self.db_path)
            clock = fixed_clock()
            barrier.wait()
            try:
                O.redeem(conn, clock, self.code, gate)
                results.append((gate, "ok"))
            except O.ReplayError:
                results.append((gate, "replay"))
            except Exception as e:  # noqa: BLE001
                results.append((gate, f"other:{e}"))
            finally:
                conn.close()

        t1 = threading.Thread(target=worker, args=("east",))
        t2 = threading.Thread(target=worker, args=("west",))
        t1.start(); t2.start(); t1.join(); t2.join()

        self.assertEqual(sorted(r[1] for r in results), ["ok", "replay"])
        check = connect(self.db_path)
        entries = check.execute(
            "SELECT gate FROM entries e JOIN tickets t ON t.id=e.ticket_id "
            "WHERE t.code=?", (self.code,)).fetchall()
        self.assertEqual(len(entries), 1)
        t = check.execute("SELECT status FROM tickets WHERE code=?",
                          (self.code,)).fetchone()
        self.assertEqual(t["status"], "redeemed")
        check.close()

    def test_redeem_and_reschedule_cannot_both_succeed(self):
        outcomes = []
        barrier = threading.Barrier(2)

        def redeem_worker():
            conn = connect(self.db_path)
            barrier.wait()
            try:
                O.redeem(conn, fixed_clock(), self.code, "east")
                outcomes.append("redeem_ok")
            except Exception:  # noqa: BLE001
                outcomes.append("redeem_fail")
            conn.close()

        def reschedule_worker():
            conn = connect(self.db_path)
            barrier.wait()
            try:
                T.reschedule(conn, fixed_clock(), self.code,
                             "2026-10-03", "08:30-11:00")
                outcomes.append("resched_ok")
            except Exception:  # noqa: BLE001
                outcomes.append("resched_fail")
            conn.close()

        t1 = threading.Thread(target=redeem_worker)
        t2 = threading.Thread(target=reschedule_worker)
        t1.start(); t2.start(); t1.join(); t2.join()

        self.assertEqual(len(outcomes), 2)
        self.assertNotEqual(sorted(outcomes), ["redeem_ok", "resched_ok"],
                            "核销与改期不能同时成功")
        check = connect(self.db_path)
        n_entries = check.execute(
            "SELECT COUNT(*) c FROM entries e JOIN tickets t ON t.id=e.ticket_id "
            "WHERE t.code=?", (self.code,)).fetchone()["c"]
        active_bookings = check.execute(
            "SELECT COUNT(*) c FROM bookings b JOIN tickets t ON t.id=b.ticket_id "
            "WHERE t.code=? AND b.status IN ('booked','checked_in')",
            (self.code,)).fetchone()["c"]
        self.assertEqual(active_bookings, 1)   # 无论谁赢，都只有一个有效预约
        self.assertIn(n_entries, (0, 1))
        check.close()


class TestTransfer(FarmTestBase):
    """券转赠留痕；已核销不可转赠。"""

    def test_gift_records_original_holder(self):
        code = self.buy()
        T.transfer(self.conn, self.clock, code, "受赠人乙", "13900000099")
        t = self.conn.execute("SELECT * FROM tickets WHERE code=?",
                              (code,)).fetchone()
        self.assertEqual(t["holder_name"], "受赠人乙")
        self.assertIn("游客甲", t["gifted_from"])

    def test_gift_after_redeem_rejected(self):
        code = self.buy()
        self.book_today(code)
        O.redeem(self.conn, self.clock, code, "east")
        with self.assertRaises(TicketStateError):
            T.transfer(self.conn, self.clock, code, "丙", "137")


if __name__ == "__main__":
    unittest.main()
