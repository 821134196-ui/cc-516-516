"""时钟抽象：测试可注入固定时间。"""
from datetime import datetime, date


class Clock:
    def __init__(self, fixed=None):
        self._fixed = fixed

    def now(self) -> datetime:
        return self._fixed or datetime.now()

    def today(self) -> date:
        return self.now().date()

    def iso(self) -> str:
        return self.now().strftime("%Y-%m-%d %H:%M:%S")

    def date_iso(self) -> str:
        return self.now().strftime("%Y-%m-%d")
