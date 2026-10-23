"""事件表契约。

与驱动数据一样，积分器每天向事件源索取一次当日事件，放进
``StepContext.events``；过程模块只从 ``ctx.events`` 取用，不读文件。

事件是**稀疏**的：十年 3650 天里，真正有施肥的天数通常不到 20 天。
所以事件源是字典索引，不是逐日序列。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping, Protocol

EVENTS_FERTILIZER = "fertilizer"
EVENTS_IRRIGATION = "irrigation"
EVENTS_HARVEST = "harvest"
EVENTS_STRAW = "straw"


class EventSchedule(Protocol):
    def at(self, day: date) -> Mapping[str, Any]: ...


class NullEvents:
    def at(self, day: date) -> Mapping[str, Any]:
        return {}


NULL_EVENTS = NullEvents()


@dataclass(frozen=True)
class CombinedEvents:
    """把多张事件表合成一个日程。

    合并时不做深合并：同一键出现两次视为配置错误——目前每个过程
    只对应一张表。
    """

    schedules: tuple[EventSchedule, ...] = ()

    def at(self, day: date) -> Mapping[str, Any]:
        merged: dict[str, Any] = {}
        for schedule in self.schedules:
            for key, value in schedule.at(day).items():
                if key in merged:
                    raise ValueError(f"事件键 {key!r} 被多张表重复提供")
                merged[key] = value
        return merged