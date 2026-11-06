"""侵蚀模块：颗粒态（式 22，ADR-0012）。

```text
F_erosion[e] = E_soil · ER[e] · C_total[e]
E_soil       = rate[kg·m⁻²·a⁻¹] / 365
```

年速率均匀摊到每日：年总量严格等于用户给的年速率，不引入未经验证的
日内形状假设。速率与富集比都来自参数库，无默认值。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from hmfms.core.registry import Direction, StepContext
from hmfms.core.state import FluxDict
from hmfms.utils.units import DAYS_PER_YEAR


@dataclass(frozen=True)
class ErosionModule:
    """``enrichment_ratio`` 只包含启用了侵蚀的元素。"""

    rate_kg_m2_a: float
    enrichment_ratio: Mapping[str, float]
    name: str = "erosion"
    direction: Direction = "out"
    depends_on_state: bool = True

    def compute(self, ctx: StepContext) -> FluxDict:
        daily_soil_loss = self.rate_kg_m2_a / DAYS_PER_YEAR
        concentration = ctx.state.concentration()
        return {
            element: {
                layer_id: (
                    daily_soil_loss
                    * self.enrichment_ratio[element]
                    * concentration[element][layer_id]
                    if element in self.enrichment_ratio
                    else 0.0
                )
                for layer_id in ctx.layer_ids
            }
            for element in ctx.elements
        }