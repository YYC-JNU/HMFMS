"""施肥输入模块。

``F[mg·m⁻²] = Σ R[kg·ha⁻¹] × C[mg·kg⁻¹] / 10000``

施用量与含量的单位归一、以及"哪个品类用哪个含量"的决策，全部在装载期
完成；本模块只做乘法（ADR-0010）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from hmfms.core.events import EVENTS_FERTILIZER
from hmfms.core.registry import Direction, StepContext
from hmfms.core.state import FluxDict
from hmfms.utils.units import fertilizer_mass_per_area


@dataclass(frozen=True)
class FertilizationModule:
    name: str = "fertilization"
    direction: Direction = "in"
    depends_on_state: bool = False

    def compute(self, ctx: StepContext) -> FluxDict:
        events = ctx.events.get(EVENTS_FERTILIZER, ())
        totals: dict[str, list[float]] = {element: [] for element in ctx.elements}
        for event in events:
            for element in ctx.elements:
                totals[element].append(
                    fertilizer_mass_per_area(
                        event.amount_kg_ha, event.concentration_mg_kg[element]
                    )
                )
        return {
            element: {
                layer_id: math.fsum(totals[element])
                for layer_id in ctx.layer_ids
            }
            for element in ctx.elements
        }