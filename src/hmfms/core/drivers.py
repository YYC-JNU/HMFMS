"""外部驱动数据的契约。

积分器每天向驱动对象索取一次当日数据，放进 ``StepContext.drivers``；
过程模块只通过 ``ctx.drivers`` 取用，不直接读文件。

键名集中在这里，避免"模块写一个字符串、IO 层写另一个"的静默错配。
"""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping, Protocol

DRIVER_DEPOSITION_DRY = "deposition_dry"
DRIVER_DEPOSITION_WET = "deposition_wet"


class DailyDriver(Protocol):
    def at(self, day: date) -> Mapping[str, Any]: ...


class NullDrivers:
    """没有任何外部驱动的场景（最小纵切、单元测试）。"""

    def at(self, day: date) -> Mapping[str, Any]:
        return {}


NULL_DRIVERS = NullDrivers()