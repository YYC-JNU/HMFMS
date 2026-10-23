"""作物收获模块。

``F[mg·m⁻²] = Σ_organ  Y[kg·ha⁻¹] × C_organ[mg·kg⁻¹] × f_removal / 10000``

``C_organ`` 由两种模式之一给出（同一个元素只能选一种）：

* ``concentration`` —— 事件装载期已把它解析成固定含量，本模块只做乘法；
* ``bcf`` —— 器官含量 = BCF × **当日快照**的土壤总量含量（式 15）。

BCF 让本模块成为**状态反馈**模块，故 ``depends_on_state`` 为真（ADR-0007）。
快照语义保证同日多个器官都读当日起始状态，不出现"先扣籽粒再算秸秆"的顺序依赖。
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
    """``bcf_elements`` 决定哪些元素按状态反馈计算。"""

    bcf_elements: frozenset[str] = frozenset()
    name: str = "harvest"
    direction: Direction = "out"

    @property
    def depends_on_state(self) -> bool:
        return bool(self.bcf_elements)

    def compute(self, ctx: StepContext) -> FluxDict:
        events = ctx.events.get(EVENTS_HARVEST, ())
        concentration = ctx.state.concentration()

        totals: dict[str, dict[int, list[float]]] = {
            element: {layer_id: [] for layer_id in ctx.layer_ids}
            for element in ctx.elements
        }
        for event in events:
            for element in ctx.elements:
                bcf = event.bcf.get(element)
                for layer_id in ctx.layer_ids:
                    if bcf is None:
                        organ_concentration = event.concentration_mg_kg[element]
                    else:
                        organ_concentration = bcf * concentration[element][layer_id]
                    removed = mass_per_area_from_rate(
                        event.yield_kg_ha, organ_concentration
                    )
                    totals[element][layer_id].append(
                        removed * event.removal_fraction
                    )
        return {
            element: {
                layer_id: math.fsum(values)
                for layer_id, values in totals[element].items()
            }
            for element in ctx.elements
        }
