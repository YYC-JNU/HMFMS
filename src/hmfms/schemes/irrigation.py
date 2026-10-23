"""灌溉输入模块。

``F[mg·m⁻²] = Σ I[mm] × C[mg·L⁻¹]``

因为 ``1 mm = 1 L·m⁻²``，所以 ``mm × mg/L`` 直接得到 ``mg/m²``。
灌溉量与降雨无关——降雨是气象驱动，本模块不读它（`INV-1` 的同族约束）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from hmfms.core.events import EVENTS_IRRIGATION
from hmfms.core.registry import Direction, StepContext
from hmfms.core.state import FluxDict
from hmfms.utils.units import irrigation_mass_per_area


@dataclass(frozen=True)
class IrrigationModule:
    name: str = "irrigation"
    direction: Direction = "in"
    depends_on_state: bool = False

    def compute(self, ctx: StepContext) -> FluxDict:
        events = ctx.events.get(EVENTS_IRRIGATION, ())
        totals: dict[str, list[float]] = {element: [] for element in ctx.elements}
        for event in events:
            for element in ctx.elements:
                totals[element].append(
                    irrigation_mass_per_area(
                        event.amount_mm, event.concentration_mg_l[element]
                    )
                )
        return {
            element: {
                layer_id: math.fsum(totals[element]) for layer_id in ctx.layer_ids
            }
            for element in ctx.elements
        }