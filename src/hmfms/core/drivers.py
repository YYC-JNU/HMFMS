"""外部驱动数据的契约。

积分器每天向驱动对象索取一次当日数据，放进 ``StepContext.drivers``；
过程模块只通过 ``ctx.drivers`` 取用，不直接读文件。

键名集中在这里，避免"模块写一个字符串、IO 层写另一个"的静默错配。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping, Protocol

DRIVER_DEPOSITION_DRY = "deposition_dry"
DRIVER_DEPOSITION_WET = "deposition_wet"
DRIVER_PRECIPITATION = "precipitation_mm"
DRIVER_ET = "evapotranspiration_mm"


class DailyDriver(Protocol):
    def at(self, day: date) -> Mapping[str, Any]: ...


class NullDrivers:
    """没有任何外部驱动的场景（最小纵切、单元测试）。"""

    def at(self, day: date) -> Mapping[str, Any]:
        return {}


NULL_DRIVERS = NullDrivers()


@dataclass(frozen=True)
class CombinedDrivers:
    """把多个逐日驱动源合成一个。

    同一个键被两个源提供视为配置错误：沉降与气象是两条独立数据流
    （``INV-1``），键名重叠意味着有人把两件事接在了一起。
    """

    sources: tuple[DailyDriver, ...] = ()

    def at(self, day: date) -> Mapping[str, Any]:
        merged: dict[str, Any] = {}
        for source in self.sources:
            for key, value in source.at(day).items():
                if key in merged:
                    raise ValueError(
                        f"驱动键 {key!r} 被多个数据源重复提供；"
                        "沉降与气象必须各自使用独立的键。"
                    )
                merged[key] = value
        return merged