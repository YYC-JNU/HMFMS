"""命令行入口。

``hmfms run <model.yaml>``：读配置 → 积分 → 写输出。
路径相对于**配置文件所在目录**解析，不受当前工作目录影响。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from hmfms.config.loader import (
    load_field_config,
    load_model_config,
    load_soil_profile,
)
from hmfms.core.drivers import DailyDriver
from hmfms.core.events import CombinedEvents, EventSchedule
from hmfms.core.integrator import DayRecord, integrate
from hmfms.core.registry import FluxModule, build_default_registry
from hmfms.core.state import State
from hmfms.io.deposition import load_deposition
from hmfms.io.events import (
    HarvestSchedule,
    StrawSchedule,
    load_fertilizer_events,
    load_harvest_events,
    load_irrigation_events,
    load_straw_events,
)
from hmfms.io.outputs import write_daily_csv, write_parameters_csv
from hmfms.parameters.library import load_library
from hmfms.parameters.resolver import ParameterResolver
from hmfms.schemes.deposition import DepositionModule
from hmfms.schemes.fertilization import FertilizationModule
from hmfms.schemes.harvest import HarvestModule
from hmfms.schemes.irrigation import IrrigationModule
from hmfms.schemes.straw import StrawRemovalModule, StrawReturnModule

DAILY_FILENAME = "daily.csv"
PARAMETERS_FILENAME = "resolved_parameters.csv"


@dataclass(frozen=True)
class RunResult:
    records: list[DayRecord]
    state: State
    output_path: Path
    parameters_path: Path


def _resolve(base: Path, candidate: Path) -> Path:
    return candidate if candidate.is_absolute() else (base / candidate)


def _reject_double_handled_organs(
    harvest: HarvestSchedule | None, straw: StrawSchedule | None
) -> None:
    """一个器官只能由一种机制处理，否则同一份质量会被扣两次。"""
    if harvest is None or straw is None:
        return
    harvested = {
        (event.crop_id, event.organ)
        for events in harvest.by_day.values()
        for event in events
    }
    returned = {
        (event.crop_id, event.organ)
        for events in straw.by_day.values()
        for event in events
    }
    overlap = sorted(harvested & returned)
    if overlap:
        listed = "、".join(f"{crop}/{organ}" for crop, organ in overlap)
        raise ValueError(
            f"以下作物器官同时出现在收获表与秸秆表：{listed}。"
            "同一器官只能由一种机制处理——籽粒等直接移除的器官放收获表，"
            "秸秆等需要按比例还田的器官放秸秆表，否则会被扣两次。"
        )


def run_from_config(
    config_path: Path, modules: tuple[FluxModule, ...] | None = None
) -> RunResult:
    config_path = Path(config_path).resolve()
    base = config_path.parent

    model = load_model_config(config_path)
    # 田块配置在 T02 只做装载与校验：location 在 T11 的 NetCDF 提取中才会被用到，
    # area_ha 目前仅作为元数据保存。保留是因为它们是田块的固有属性，不是死配置。
    load_field_config(_resolve(base, model.field))

    library = (
        load_library(_resolve(base, model.parameters.library))
        if model.parameters is not None
        else {}
    )
    resolver = ParameterResolver(library)

    soil = load_soil_profile(
        _resolve(base, model.inputs.soil.file),
        model.elements,
        model.inputs.soil.units,
        resolver=resolver,
    )

    drivers: DailyDriver | None = None
    overrides: dict[str, FluxModule] = {}
    deposition = model.inputs.deposition
    if deposition is not None:
        drivers = load_deposition(
            _resolve(base, deposition.file),
            deposition,
            model.elements,
            model.time.start,
            model.time.end,
        )
        overrides["deposition"] = DepositionModule()

    events: EventSchedule | None = None
    schedules: list[EventSchedule] = []
    harvest_schedule = None
    straw_schedule = None
    management = model.management
    if management is not None:
        if management.fertilizer_events is not None:
            schedules.append(
                load_fertilizer_events(
                    _resolve(base, management.fertilizer_events.file),
                    management.fertilizer_events,
                    model.elements,
                    resolver,
                    model.time.start,
                    model.time.end,
                )
            )
            overrides["fertilization"] = FertilizationModule()
        if management.irrigation_events is not None:
            schedules.append(
                load_irrigation_events(
                    _resolve(base, management.irrigation_events.file),
                    management.irrigation_events,
                    model.elements,
                    resolver,
                    model.time.start,
                    model.time.end,
                )
            )
            overrides["irrigation"] = IrrigationModule()
        if management.harvest_events is not None:
            harvest_schedule = load_harvest_events(
                _resolve(base, management.harvest_events.file),
                management.harvest_events,
                model.elements,
                resolver,
                model.time.start,
                model.time.end,
            )
            schedules.append(harvest_schedule)
            overrides["harvest"] = HarvestModule()
        if management.straw_events is not None:
            straw_schedule = load_straw_events(
                _resolve(base, management.straw_events.file),
                management.straw_events,
                model.elements,
                resolver,
                model.time.start,
                model.time.end,
            )
            schedules.append(straw_schedule)
            overrides["straw_return"] = StrawReturnModule()
            overrides["straw_removal"] = StrawRemovalModule()
        _reject_double_handled_organs(harvest_schedule, straw_schedule)
    if schedules:
        events = CombinedEvents(tuple(schedules))

    registry = build_default_registry(overrides) if modules is None else modules
    records, state = integrate(model, soil, registry, drivers, events)

    output_directory = _resolve(base, model.output.directory)
    output_path = write_daily_csv(
        output_directory / DAILY_FILENAME,
        records,
        model.elements,
        state.layer_ids,
    )
    parameters_path = write_parameters_csv(
        output_directory / PARAMETERS_FILENAME, resolver.records()
    )
    return RunResult(
        records=records,
        state=state,
        output_path=output_path,
        parameters_path=parameters_path,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hmfms", description="农田重金属通量模型")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="按配置文件运行模型")
    run_parser.add_argument("config", type=Path, help="主配置文件（model.yaml）")

    args = parser.parse_args(argv)

    if args.command == "run":
        result = run_from_config(args.config)
        last = result.records[-1]
        print(f"输出: {result.output_path}")
        print(f"参数溯源: {result.parameters_path}")
        print(f"天数: {len(result.records)}")
        for element in result.state.elements:
            print(
                f"  {element}: 期末储量 {last.mass[element]} mg/m2；"
                f"累积输入 {last.cumulative_in[element]:.6g} mg/m2；"
                f"累积输出 {last.cumulative_out[element]:.6g} mg/m2；"
                f"闭合误差 {last.closure[element]:.3g} mg/m2"
            )
        return 0

    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())