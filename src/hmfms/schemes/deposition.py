"""大气沉降输入模块。

``INV-1``：通量只来自文件，**不由降雨推导**。若输入文件给的是"降水中浓度"，
换算应在装载阶段完成（``hmfms.io.deposition``），不属于本模块的职责。
"""

from __future__ import annotations

from dataclasses import dataclass

from hmfms.core.drivers import DRIVER_DEPOSITION_DRY, DRIVER_DEPOSITION_WET
from hmfms.core.registry import Direction, StepContext
from hmfms.core.state import FluxDict


@dataclass(frozen=True)
class DepositionModule:
    """干沉降 + 湿沉降，逐日直读。"""

    name: str = "deposition"
    direction: Direction = "in"
    depends_on_state: bool = False

    def compute(self, ctx: StepContext) -> FluxDict:
        for key in (DRIVER_DEPOSITION_DRY, DRIVER_DEPOSITION_WET):
            if key not in ctx.drivers:
                raise ValueError(
                    f"deposition 模块需要驱动 {key!r}，但当日 drivers 中只有 "
                    f"{sorted(ctx.drivers)}。若配置声明了沉降输入，"
                    "请检查装载阶段是否把它接进了积分器。"
                )
        dry = ctx.drivers[DRIVER_DEPOSITION_DRY]
        wet = ctx.drivers[DRIVER_DEPOSITION_WET]
        return {
            element: {
                layer_id: dry[element] + wet[element]
                for layer_id in ctx.layer_ids
            }
            for element in ctx.elements
        }