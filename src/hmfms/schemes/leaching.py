"""淋溶输出模块（式 17/18，ADR-0011）。

```text
F_leach[e] = q_leach · f_exch[e] · C_total[e] / Kd[e]
```

- ``q_leach`` 来自当日水量平衡（``core/water.percolation_mm``），单位 mm·d⁻¹；
- ``Kd`` 的口径由 ``leaching.kd_basis`` 显式声明：
  ``total`` 时 ``f_exch = 1``，``labile`` 时必须另行提供 ``f_exch``；
- 本模块是**状态反馈**模块：读当日快照含量（ADR-0007）。

单位自洽：``mm·d⁻¹`` 即 ``L·m⁻²·d⁻¹``；``C_total`` 为 mg·kg⁻¹、
``Kd`` 为 L·kg⁻¹，相除得 mg·L⁻¹；再乘水量即 mg·m⁻²·d⁻¹。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

from hmfms.core.drivers import DRIVER_ET, DRIVER_PRECIPITATION
from hmfms.core.events import EVENTS_IRRIGATION
from hmfms.core.registry import Direction, StepContext
from hmfms.core.state import FluxDict
from hmfms.core.water import split_excess
from hmfms.schemes.transport import pore_water_concentration


@dataclass(frozen=True)
class LeachingModule:
    """``kd_l_per_kg`` / ``f_exch`` 只包含启用了输移的元素。"""

    kd_l_per_kg: Mapping[str, float]
    f_exch: Mapping[str, float]
    runoff_fraction: float = 0.0
    name: str = "leaching"
    direction: Direction = "out"
    depends_on_state: bool = True

    def compute(self, ctx: StepContext) -> FluxDict:
        precipitation = ctx.drivers.get(DRIVER_PRECIPITATION)
        if precipitation is None:
            raise ValueError(
                "淋溶需要逐日降水量驱动（inputs.weather）；"
                "当前运行的驱动里没有降水键。"
                "请在 model.yaml 增加 inputs.weather，或关闭该元素的淋溶。"
            )
        evapotranspiration = ctx.drivers.get(DRIVER_ET, 0.0)
        irrigation = math.fsum(
            event.amount_mm for event in ctx.events.get(EVENTS_IRRIGATION, ())
        )
        _, percolation = split_excess(
            precipitation_mm=precipitation,
            irrigation_mm=irrigation,
            evapotranspiration_mm=evapotranspiration,
            runoff_fraction=self.runoff_fraction,
        )
        concentration = ctx.state.concentration()

        return {
            element: {
                layer_id: (
                    percolation
                    * pore_water_concentration(
                        concentration_mg_kg=concentration[element][layer_id],
                        f_exch=self.f_exch[element],
                        kd_l_per_kg=self.kd_l_per_kg[element],
                    )
                    if element in self.kd_l_per_kg
                    else 0.0
                )
                for layer_id in ctx.layer_ids
            }
            for element in ctx.elements
        }