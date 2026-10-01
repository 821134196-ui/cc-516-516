"""模拟适配器：电子秤与支付网关。

- 电子秤可被设置为断连状态；现场端轮询读数，断连时给出明确错误，不得手工录入。
- 支付适配器模拟即时成功/失败，退款原路返回；断网场景可挂 pending（离园后补扣款）。
"""
from __future__ import annotations

import itertools
import time
from dataclasses import dataclass


class ScaleError(Exception):
    """电子秤异常（断连/读数不稳）。"""


class ScaleAdapter:
    """模拟蓝牙电子秤。单例，管理端/测试可切换连接状态与读数。"""

    def __init__(self, sn: str = "SCALE-MOCK-01") -> None:
        self.sn = sn
        self._connected = True
        self._stable = True
        self._weight_g = 0

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    def set_weight_g(self, grams: int, stable: bool = True) -> None:
        self._weight_g = int(grams)
        self._stable = stable

    def read(self) -> int:
        """返回稳定克重；断连或读数不稳时抛 ScaleError（禁止人工估重）。"""
        if not self._connected:
            raise ScaleError("电子秤已断连，请检查蓝牙连接后重新读数")
        if not self._stable:
            raise ScaleError("读数尚未稳定，请稍候")
        if self._weight_g < 0:
            raise ScaleError("读数异常（负值），请检查秤盘")
        return self._weight_g


@dataclass
class PayResult:
    ok: bool
    ref: str
    status: str  # success / failed / pending
    amount_cents: int
    message: str = ""


class PayAdapter:
    """模拟支付：逐笔生成流水号；可切换网关在线状态。"""

    def __init__(self, channel: str = "mock-pay") -> None:
        self.channel = channel
        self.online = True
        self._seq = itertools.count(1)

    def _ref(self, kind: str) -> str:
        return f"MOCK-{kind.upper()}-{int(time.time())}-{next(self._seq):04d}"

    def charge(self, amount_cents: int, note: str = "") -> PayResult:
        if amount_cents <= 0:
            return PayResult(True, self._ref("ZF"), "success", 0, "无需扣款")
        if not self.online:
            # 网关不可用：挂账，离园后补扣
            return PayResult(False, self._ref("BC"), "pending", amount_cents,
                             "支付网关暂不可用，已挂账待补扣")
        return PayResult(True, self._ref("ZF"), "success", amount_cents, note or "扣款成功")

    def refund(self, amount_cents: int, note: str = "") -> PayResult:
        if amount_cents < 0:
            return PayResult(False, "", "failed", amount_cents, "退款金额非法")
        if not self.online:
            return PayResult(False, self._ref("TK"), "pending", amount_cents,
                             "支付网关暂不可用，退款已排队")
        return PayResult(True, self._ref("TK"), "success", amount_cents,
                         note or "原路退款成功")


# 进程内单例（服务端与测试共享同一模拟设备）
scale = ScaleAdapter()
pay = PayAdapter()
