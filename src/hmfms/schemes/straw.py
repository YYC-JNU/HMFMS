"""秸秆还田与外运。

一个秸秆事件同时产生两项通量（equations.md 式 10 与式 12）：

```
还田 = 总带出量 × f_return            （输入）
外运 = 总带出量 × (1 − f_return)      （输出）
```

两式之和恒等于总带出量——这是 **`INV-2`**。

两个模块从**同一个总带出量**拆分，所以不变量由构造保证；同时每一步做一次
数值断言，防止某天有人改了其中一侧的系数而另一侧没跟上。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from hmfms.core.events import EVENTS_STRAW
from hmfms.core.registry import Direction, StepContext
from hmfms.core.state import FluxDict

# 数值容差：拆分涉及两组 fsum，末几位必然有差（A 类，非物理参数）
INV2_TOLERANCE = 1e-12


def split_straw(
    events: tuple, elements: tuple[str, ...]
) -> tuple[dict[str, float], dict[str, float]]:
    """把秸秆事件拆成（还田, 外运），并断言 `INV-2`。"""
    returned: dict[str, float] = {}
    removed: dict[str, float] = {}

    for element in elements:
        total = math.fsum(event.mass_mg_m2[element] for event in events)
        back = math.fsum(
            event.mass_mg_m2[element] * event.return_fraction for event in events
        )
        away = math.fsum(
            event.mass_mg_m2[element] * (1.0 - event.return_fraction)
            for event in events
        )
        scale = max(abs(total), 1.0)
        if abs(back + away - total) > INV2_TOLERANCE * scale:
            raise ValueError(
                f"INV-2 被破坏：元素 {element} 还田 {back:.12g} + 外运 "
                f"{away:.12g} ≠ 总带出量 {total:.12g}。"
                "还田与外运必须严格互补。"
            )
        returned[element] = back
        removed[element] = away
    return returned, removed


@dataclass(frozen=True)
class StrawReturnModule:
    """还田部分计入输入通量。"""

    name: str = "straw_return"
    direction: Direction = "in"
    depends_on_state: bool = False

    def compute(self, ctx: StepContext) -> FluxDict:
        events = ctx.events.get(EVENTS_STRAW, ())
        returned, _ = split_straw(events, ctx.elements)
        return {
            element: {
                layer_id: returned[element] for layer_id in ctx.layer_ids
            }
            for element in ctx.elements
        }


@dataclass(frozen=True)
class StrawRemovalModule:
    """未还田部分计入输出通量。"""

    name: str = "straw_removal"
    direction: Direction = "out"
    depends_on_state: bool = False

    def compute(self, ctx: StepContext) -> FluxDict:
        events = ctx.events.get(EVENTS_STRAW, ())
        _, removed = split_straw(events, ctx.elements)
        return {
            element: {layer_id: removed[element] for layer_id in ctx.layer_ids}
            for element in ctx.elements
        }