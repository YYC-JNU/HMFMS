"""过程模块：每个外部输入或输出通量一个模块。

模块必须实现 ``hmfms.core.registry.FluxModule`` 契约：
输入 ``StepContext``（状态快照 + 当日驱动 + 当日事件），
输出 ``{element: {layer: mg·m⁻²·d⁻¹}}``。
"""

from hmfms.schemes.deposition import DepositionModule
from hmfms.schemes.fertilization import FertilizationModule
from hmfms.schemes.harvest import HarvestModule
from hmfms.schemes.irrigation import IrrigationModule
from hmfms.schemes.straw import StrawRemovalModule, StrawReturnModule

__all__ = [
    "DepositionModule",
    "FertilizationModule",
    "HarvestModule",
    "IrrigationModule",
    "StrawRemovalModule",
    "StrawReturnModule",
]