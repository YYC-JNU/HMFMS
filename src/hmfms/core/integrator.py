"""时间积分器。

显式 Euler，步长 1 天，快照语义（ADR-0007）：

1. 复制当前状态作为快照；
2. 当日所有模块读同一份快照、各自返回通量；
3. 汇总后一次性写入新状态；
4. 检查非负与质量闭合。

求和一律用 ``math.fsum``：它的结果是精确和再舍入，与加法顺序无关，
因此"打乱模块顺序输出逐字节一致"不依赖任何实现细节。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Mapping

from hmfms.config.field import SoilProfile
from hmfms.config.models import ModelConfig
from hmfms.core.drivers import NULL_DRIVERS, DailyDriver
from hmfms.core.errors import FluxImbalanceError
from hmfms.core.events import NULL_EVENTS, EventSchedule
from hmfms.core.registry import (
    FLUX_PROCESSES,
    FluxModule,
    StepContext,
    compute_fluxes,
)
from hmfms.core.state import FluxDict, State

# 数值地板：起始储量为 0 时，相对误差的分母不能为 0。
# 这是数值实现细节，不是物理参数（A 类）。
_RELATIVE_FLOOR = 1e-12


@dataclass(frozen=True)
class DayRecord:
    day: date
    mass: dict[str, dict[int, float]]
    concentration: dict[str, dict[int, float]]
    fluxes: dict[str, FluxDict]
    drivers: Mapping[str, Any]
    in_total: dict[str, float]
    out_total: dict[str, float]
    net: dict[str, float]
    cumulative_in: dict[str, float]
    cumulative_out: dict[str, float]
    closure: dict[str, float]
    relative_closure: dict[str, float]


def _summarize_events(events: Mapping[str, Any]) -> tuple[str, ...]:
    """把当日事件压成几行，供通量失衡异常使用。"""
    lines: list[str] = []
    for name in sorted(events):
        items = events[name]
        if not items:
            continue
        identifiers = ", ".join(getattr(item, "event_id", "?") for item in items)
        lines.append(f"{name}: {identifiers}")
    return tuple(lines)


def integrate(
    model: ModelConfig,
    profile: SoilProfile,
    modules: tuple[FluxModule, ...],
    drivers: DailyDriver | None = None,
    events: EventSchedule | None = None,
) -> tuple[list[DayRecord], State]:
    state = State.from_profile(model.elements, profile)
    start_total = {element: state.total_mass(element) for element in model.elements}
    cumulative_in = {element: 0.0 for element in model.elements}
    cumulative_out = {element: 0.0 for element in model.elements}
    records: list[DayRecord] = []
    driver = NULL_DRIVERS if drivers is None else drivers
    schedule = NULL_EVENTS if events is None else events

    for offset in range(model.time.n_days):
        day = model.time.start + timedelta(days=offset)
        snapshot = state.snapshot()
        day_drivers = driver.at(day)
        day_events = schedule.at(day)
        fluxes = compute_fluxes(
            modules,
            StepContext(
                date=day,
                state=snapshot,
                drivers=day_drivers,
                events=day_events,
            ),
        )
        event_summary = _summarize_events(day_events)

        in_total: dict[str, float] = {}
        out_total: dict[str, float] = {}
        net: dict[str, float] = {}

        for element in model.elements:
            element_in: list[float] = []
            element_out: list[float] = []
            for layer_id in state.layer_ids:
                flux_in = {
                    name: fluxes[name][element][layer_id]
                    for name, direction in FLUX_PROCESSES
                    if direction == "in"
                }
                flux_out = {
                    name: fluxes[name][element][layer_id]
                    for name, direction in FLUX_PROCESSES
                    if direction == "out"
                }
                daily_in = math.fsum(flux_in.values())
                daily_out = math.fsum(flux_out.values())
                before = state.mass_mg_m2[element][layer_id]
                after = before + daily_in - daily_out
                if after < 0.0:
                    raise FluxImbalanceError(
                        day=day,
                        element=element,
                        layer_id=layer_id,
                        mass_before=before,
                        flux_in=flux_in,
                        flux_out=flux_out,
                        events=event_summary,
                    )
                state.mass_mg_m2[element][layer_id] = after
                element_in.append(daily_in)
                element_out.append(daily_out)

            in_total[element] = math.fsum(element_in)
            out_total[element] = math.fsum(element_out)
            net[element] = in_total[element] - out_total[element]
            cumulative_in[element] += in_total[element]
            cumulative_out[element] += out_total[element]

        closure: dict[str, float] = {}
        relative: dict[str, float] = {}
        for element in model.elements:
            residual = (
                state.total_mass(element)
                - start_total[element]
                - cumulative_in[element]
                + cumulative_out[element]
            )
            closure[element] = residual
            scale = max(abs(start_total[element]), _RELATIVE_FLOOR)
            relative[element] = abs(residual) / scale
            if relative[element] > model.closure_tolerance:
                raise ValueError(
                    f"{day} 元素 {element} 的质量闭合误差超阈值："
                    f"残差 {residual:.6g} mg/m2，"
                    f"相对 {relative[element]:.3g} > {model.closure_tolerance:g}"
                )

        records.append(
            DayRecord(
                day=day,
                mass={e: dict(layers) for e, layers in state.mass_mg_m2.items()},
                concentration=state.concentration(),
                fluxes=fluxes,
                drivers=dict(day_drivers),
                in_total=in_total,
                out_total=out_total,
                net=net,
                cumulative_in=dict(cumulative_in),
                cumulative_out=dict(cumulative_out),
                closure=closure,
                relative_closure=relative,
            )
        )

    return records, state