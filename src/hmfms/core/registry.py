"""通量模块契约与注册表。

新增过程只能**注册**新模块，不得修改积分器（ADR-0005）。

``FLUX_PROCESSES`` 是过程名与方向的**唯一来源**：输出列、注册表、日志
都从它派生，别处不得再写一遍过程名。它属于 A 类契约常量——不随田块或
元素变化，因此允许写在代码里。

首版所有过程都是零通量 stub，签名与真实模块完全一致，所以测试可以
一视同仁地遍历，不需要为 stub 写特例。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal, Mapping, Protocol

from hmfms.core.state import FluxDict, State

Direction = Literal["in", "out"]

FLUX_PROCESSES: tuple[tuple[str, Direction], ...] = (
    ("deposition", "in"),
    ("fertilization", "in"),
    ("irrigation", "in"),
    ("straw_return", "in"),
    ("harvest", "out"),
    ("straw_removal", "out"),
    ("lumped_loss", "out"),
    ("leaching", "out"),
    ("runoff", "out"),
    ("erosion", "out"),
    ("volatilization", "out"),
    ("dust_emission", "out"),
)

PROCESS_DIRECTION: dict[str, Direction] = dict(FLUX_PROCESSES)
PROCESS_NAMES: tuple[str, ...] = tuple(name for name, _ in FLUX_PROCESSES)


@dataclass(frozen=True)
class StepContext:
    """一个时间步内所有模块能看到的全部信息。

    ``state`` 是**快照**：模块既不得修改它，也看不到其他模块的结果。
    """

    date: date
    state: State
    drivers: Mapping[str, Any] = field(default_factory=dict)
    events: Mapping[str, Any] = field(default_factory=dict)

    @property
    def elements(self) -> tuple[str, ...]:
        return self.state.elements

    @property
    def layer_ids(self) -> tuple[int, ...]:
        return self.state.layer_ids


class FluxModule(Protocol):
    name: str
    direction: Direction
    depends_on_state: bool

    def compute(self, ctx: StepContext) -> FluxDict: ...


def zero_flux(elements: tuple[str, ...], layer_ids: tuple[int, ...]) -> FluxDict:
    return {element: {lid: 0.0 for lid in layer_ids} for element in elements}


@dataclass(frozen=True)
class ZeroFluxModule:
    """未实现过程的 stub：签名与真实模块一致，恒返回零。"""

    name: str
    direction: Direction
    depends_on_state: bool = False

    def compute(self, ctx: StepContext) -> FluxDict:
        return zero_flux(ctx.elements, ctx.layer_ids)


def build_default_registry(
    overrides: Mapping[str, FluxModule] | None = None,
) -> tuple[FluxModule, ...]:
    """按契约顺序装配注册表，未覆盖的过程用零通量 stub。"""
    overrides = dict(overrides or {})
    unknown = sorted(set(overrides) - set(PROCESS_NAMES))
    if unknown:
        raise ValueError(f"未登记的过程无法覆盖：{unknown}")

    modules: list[FluxModule] = []
    for name, direction in FLUX_PROCESSES:
        module = overrides.get(name)
        if module is None:
            module = ZeroFluxModule(name=name, direction=direction)
        elif module.name != name or module.direction != direction:
            raise ValueError(
                f"覆盖模块 {module.name!r} 与契约不一致："
                f"期望 ({name!r}, {direction!r})，"
                f"实际 ({module.name!r}, {module.direction!r})"
            )
        modules.append(module)
    return tuple(modules)


def compute_fluxes(
    modules: tuple[FluxModule, ...], ctx: StepContext
) -> dict[str, FluxDict]:
    """按 ``FLUX_PROCESSES`` 的**规范顺序**调用模块。

    顺序本身不影响结果（快照语义），但固定顺序让浮点求和路径唯一，
    便于"打乱注册顺序输出逐字节一致"这条验收标准成立。
    """
    by_name = {module.name: module for module in modules}
    missing = sorted(set(PROCESS_NAMES) - set(by_name))
    if missing:
        raise ValueError(f"注册表缺少过程模块：{missing}")
    extra = sorted(set(by_name) - set(PROCESS_NAMES))
    if extra:
        raise ValueError(f"注册表含未登记的过程模块：{extra}")
    return {name: by_name[name].compute(ctx) for name in PROCESS_NAMES}