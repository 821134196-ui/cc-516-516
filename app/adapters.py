"""外部设备 / 服务适配器。

- MockScale：电子秤模拟。可设置读数、断连；称重失败时业务侧必须重试，
  不得自行估计重量，也不得让检票员手工改数。
- MockPayment：支付模拟。支持扣款、退款、失败注入；返回统一交易号。
"""
import itertools
import threading


class ScaleError(Exception):
    """电子秤不可用（断连 / 读数不稳）。"""


class MockScale:
    def __init__(self):
        self._lock = threading.Lock()
        self._connected = True
        self._stable = True
        self._reading = 0.0
        self._seq = itertools.count(1)

    # ---- 演示/管理端控制 ----
    def connect(self):
        with self._lock:
            self._connected = True

    def disconnect(self):
        with self._lock:
            self._connected = False

    def set_reading(self, kg):
        with self._lock:
            self._reading = round(float(kg), 3)

    def set_stable(self, stable: bool):
        with self._lock:
            self._stable = stable

    def snapshot(self):
        with self._lock:
            return {
                "connected": self._connected,
                "stable": self._stable,
                "reading_kg": self._reading,
            }

    # ---- 现场端调用 ----
    def read(self, retries=0):
        """读取稳定重量；断连抛 ScaleError。"""
        with self._lock:
            if not self._connected:
                raise ScaleError("电子秤断连，请检查连接线后重试")
            if not self._stable:
                raise ScaleError("读数未稳定，请稍候")
            return round(self._reading, 3)


class PaymentError(Exception):
    pass


class MockPayment:
    def __init__(self):
        self._seq = itertools.count(10001)
        self.fail_next = False          # 注入：下一笔失败

    def _ref(self, kind):
        return f"MOCK-{kind.upper()}-{next(self._seq)}"

    def charge(self, amount, note=""):
        if amount <= 0:
            raise PaymentError("扣款金额必须为正")
        if self.fail_next:
            self.fail_next = False
            return {"ok": False, "ref_no": self._ref("FAIL"), "amount": amount}
        return {"ok": True, "ref_no": self._ref("PAY"), "amount": amount}

    def refund(self, amount):
        if amount <= 0:
            raise PaymentError("退款金额必须为正")
        return {"ok": True, "ref_no": self._ref("REF"), "amount": -amount}
