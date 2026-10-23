"""作物收获模块。

``F[mg·m⁻²] = Σ_organ  Y[kg·ha⁻¹] × C[mg·kg⁻¹] × f_removal / 10000``
按器官逐个计算后求和。

吸收模式（固定浓度 / BCF）的差异在装载期解决：事件带来的
``concentration_mg_kg`` 已经是最终含量，本模块只做乘法。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from hmfms.core.events import EVENTS_HARVEST
from hmfms.core.registry import Direction, StepContext
from hmfms.core.state import FluxDict
from hmfms.utils.units import mass_per_area_from_rate


@dataclass(frozen=True)
class HarvestModule:
    name: str = "harvest"
    direction: Direction = "out"
    depends_on_state: bool = False

    def compute(self, ctx: StepContext) -> FluxDict:
        events = ctx.events.get(EVENTS_HARVEST, ())
        totals: dict[str, list[float]] = {element: [] for element in ctx.elements}
        for event in events:
            for element in ctx.elements:
                removed = mass_per_area_from_rate(
                    event.yield_kg_ha, event.concentration_mg_kg[element]
                )
                totals[element].append(removed * event.removal_fraction)
        return {
            element: {
                layer_id: math.fsum(totals[element]) for layer_id in ctx.layer_ids
            }
            for element in ctx.elements
        }