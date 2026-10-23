"""模型核心：状态、模块契约、时间积分。"""

from hmfms.core.errors import FluxImbalanceError, HmfmsError
from hmfms.core.integrator import DayRecord, integrate
from hmfms.core.registry import (
    FLUX_PROCESSES,
    PROCESS_NAMES,
    FluxModule,
    StepContext,
    ZeroFluxModule,
    build_default_registry,
    compute_fluxes,
)
from hmfms.core.state import FluxDict, State

__all__ = [
    "FLUX_PROCESSES",
    "PROCESS_NAMES",
    "DayRecord",
    "FluxDict",
    "FluxImbalanceError",
    "FluxModule",
    "HmfmsError",
    "State",
    "StepContext",
    "ZeroFluxModule",
    "build_default_registry",
    "compute_fluxes",
    "integrate",
]