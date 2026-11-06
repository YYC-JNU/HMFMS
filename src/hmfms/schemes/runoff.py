"""径流模块：只记**溶解态**（式 20，ADR-0012）。

```text
F_runoff[e] = Q_runoff · C_sol[e]
C_sol       = f_exch · C_total / Kd
```

颗粒态金属随泥沙流失，统一由 `erosion` 模块记（式 22）；若两边都记，
同一份土壤会被扣两次，而闭合检查发现不了。
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
class RunoffModule:
    """``kd_l_per_kg`` / ``f_exch`` 只包含启用了径流的元素。"""

    kd_l_per_kg: Mapping[str, float]
    f_exch: Mapping[str, float]
    runoff_fraction: float
    name: str = "runoff"
    direction: Direction = "out"
    depends_on_state: bool = True

    def compute(self, ctx: StepContext) -> FluxDict:
        precipitation = ctx.drivers.get(DRIVER_PRECIPITATION)
        if precipitation is None:
            raise ValueError(
                "径流需要逐日降水量驱动（inputs.weather）；"
                "当前运行的驱动里没有降水键。"
                "请在 model.yaml 增加 inputs.weather，或关闭该元素的径流。"
            )
        evapotranspiration = ctx.drivers.get(DRIVER_ET, 0.0)
        irrigation = math.fsum(
            event.amount_mm for event in ctx.events.get(EVENTS_IRRIGATION, ())
        )
        runoff, _ = split_excess(
            precipitation_mm=precipitation,
            irrigation_mm=irrigation,
            evapotranspiration_mm=evapotranspiration,
            runoff_fraction=self.runoff_fraction,
        )
        concentration = ctx.state.concentration()

        return {
            element: {
                layer_id: (
                    runoff
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