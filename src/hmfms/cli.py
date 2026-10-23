"""命令行入口。

``hmfms run <model.yaml>``：读配置 → 积分 → 写输出。
路径相对于**配置文件所在目录**解析，不受当前工作目录影响。
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

from hmfms.config.elements import ElementConfig
from hmfms.config.models import OUTPUT_FREQUENCIES
from hmfms.config.loader import (
    load_element_configs,
    load_field_config,
    load_model_config,
    load_soil_profile,
)
from hmfms.core.drivers import DailyDriver
from hmfms.core.events import CombinedEvents, EventSchedule
from hmfms.core.integrator import DayRecord, integrate
from hmfms.core.registry import (
    FluxModule,
    ZeroFluxModule,
    build_default_registry,
)
from hmfms.core.state import State
from hmfms.io.deposition import DepositionSeries, load_deposition
from hmfms.io.events import (
    HarvestSchedule,
    StrawSchedule,
    load_fertilizer_events,
    load_harvest_events,
    load_irrigation_events,
    load_straw_events,
)
from hmfms.io.fingerprint import (
    config_fingerprint,
    deposition_material,
    model_material,
)
from hmfms.io.netcdf_deposition import extract_deposition_csv
from hmfms.io.outputs import (
    PLACEHOLDER_SUFFIX,
    tabulate,
    write_parameters_csv,
    write_table_csv,
)
from hmfms.parameters.library import load_library
from hmfms.parameters.resolver import ParameterResolver
from hmfms.schemes.deposition import DepositionModule
from hmfms.schemes.fertilization import FertilizationModule
from hmfms.schemes.harvest import HarvestModule
from hmfms.schemes.irrigation import IrrigationModule
from hmfms.schemes.straw import StrawRemovalModule, StrawReturnModule
from hmfms.validation import run_validation

TABLE_FILENAMES: dict[str, str] = {
    frequency: f"{frequency}.csv" for frequency in OUTPUT_FREQUENCIES
}
PARAMETERS_FILENAME = "resolved_parameters.csv"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunResult:
    records: list[DayRecord]
    state: State
    output_paths: dict[str, Path]
    parameters_path: Path
    config_fingerprint: str

    @property
    def output_path(self) -> Path:
        """兼容旧调用：优先返回日表。"""
        return self.output_paths.get("daily") or next(iter(self.output_paths.values()))


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


HYDROLOGY_PROCESSES: tuple[str, ...] = ("leaching", "runoff", "erosion")


def _log_process_caveats(
    modules: tuple[FluxModule, ...],
    element_configs: dict[str, ElementConfig],
) -> None:
    """关闭水文过程时的“累积上限”提示与按元素警告强度。

    强度与文案来自元素配置的 ``diagnostics``，代码里不写元素名
    （HARDCODE_AUDIT R1）。
    """
    disabled = {
        module.name for module in modules if isinstance(module, ZeroFluxModule)
    }
    if all(name in disabled for name in HYDROLOGY_PROCESSES):
        logger.warning(
            "在未启用淋溶、径流、侵蚀的条件下，"
            "本结果为土壤重金属累积的上限。"
        )

    if "runoff" not in disabled:
        return
    for element in element_configs:
        diagnostics = element_configs[element].diagnostics
        note = (
            diagnostics.runoff_disabled_note
            or "径流未启用，累积可能高估。"
        )
        if diagnostics.runoff_disabled_severity == "strong":
            logger.warning("强警告：元素 %s 关闭径流。%s", element, note)
        else:
            logger.warning("一般警告：元素 %s 关闭径流。%s", element, note)


def _warn_bcf_ignores_straw(
    element_configs: dict[str, ElementConfig], straw: StrawSchedule | None
) -> None:
    """v1 的 BCF 模式只作用于收获表；秸秆表仍用固定浓度，必须说出来。"""
    if straw is None:
        return
    bcf_elements = sorted(
        element
        for element, config in element_configs.items()
        if config.uses_state
    )
    if not bcf_elements:
        return
    logger.warning(
        "元素 %s 使用 bcf 模式，但秸秆表仍按固定浓度处理"
        "（crop.<作物>.<器官>.<元素>）。v1 的 BCF 只覆盖收获表，"
        "若秸秆也必须按土壤含量计算，请改用 concentration 模式并自行换算。",
        ", ".join(bcf_elements),
    )


def run_from_config(
    config_path: Path,
    modules: tuple[FluxModule, ...] | None = None,
    *,
    allow_placeholder: bool = False,
) -> RunResult:
    config_path = Path(config_path).resolve()
    base = config_path.parent

    input_paths: list[Path] = [config_path]

    def _input(candidate: Path) -> Path:
        """解析一个输入文件并登记进配置指纹。"""
        path = _resolve(base, candidate)
        input_paths.append(path)
        return path

    model = load_model_config(config_path)
    # 田块配置在 T02 只做装载与校验：location 在 T11 的 NetCDF 提取中才会被用到，
    # area_ha 目前仅作为元数据保存。保留是因为它们是田块的固有属性，不是死配置。
    field_path = _input(model.field)
    field = load_field_config(field_path)

    library = {}
    if model.parameters is not None:
        library_path = _input(model.parameters.library)
        library = load_library(library_path)
    resolver = ParameterResolver(library, allow_placeholder=allow_placeholder)

    element_configs = load_element_configs(base, model)
    configured_paths = model.element_configs or {}
    for relative in configured_paths.values():
        _input(relative)
    for element in model.elements:
        config = element_configs[element]
        resolver.record_choice(
            key=f"crop_uptake.mode.{element}",
            choice=config.uptake_mode,
            source=(
                str(configured_paths[element])
                if element in configured_paths
                else "未提供元素配置文件（默认 concentration）"
            ),
            note=f"{config.uptake_mode}; basis={config.crop_uptake.basis}",
        )

    soil_path = _input(model.inputs.soil.file)
    soil = load_soil_profile(
        soil_path,
        model.elements,
        model.inputs.soil.units,
        resolver=resolver,
    )

    drivers: DailyDriver | None = None
    deposition_series: DepositionSeries | None = None
    deposition_path: Path | None = None
    overrides: dict[str, FluxModule] = {}
    deposition = model.inputs.deposition
    if deposition is not None:
        deposition_path = _input(deposition.file)
        if deposition.format == "netcdf":
            netcdf_spec = deposition.netcdf
            if netcdf_spec is None:  # 配置层已保证，这里仅作类型收窄
                raise ValueError("format: netcdf 但缺少 inputs.deposition.netcdf")
            consumed = extract_deposition_csv(
                deposition_path,
                _resolve(base, netcdf_spec.cache),
                deposition,
                model.elements,
                field.location.latitude,
                field.location.longitude,
            )
        else:
            consumed = deposition_path
        deposition_series = load_deposition(
            consumed,
            deposition,
            model.elements,
            model.time.start,
            model.time.end,
        )
        drivers = deposition_series
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
                    _input(management.fertilizer_events.file),
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
                    _input(management.irrigation_events.file),
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
                _input(management.harvest_events.file),
                management.harvest_events,
                model.elements,
                resolver,
                model.time.start,
                model.time.end,
                uptake=element_configs,
            )
            schedules.append(harvest_schedule)
            overrides["harvest"] = HarvestModule(
                frozenset(
                    element
                    for element, config in element_configs.items()
                    if config.uses_state
                )
            )
        if management.straw_events is not None:
            straw_schedule = load_straw_events(
                _input(management.straw_events.file),
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
        _warn_bcf_ignores_straw(element_configs, straw_schedule)
    if schedules:
        events = CombinedEvents(tuple(schedules))

    if resolver.has_placeholders:
        placeholder_keys = resolver.placeholder_keys()
        logger.warning(
            "占位模式：%d 个参数使用占位值 0，全部数值列加 %s 后缀，仅供结构演示，"
            "不得用于正式结论。",
            len(placeholder_keys),
            PLACEHOLDER_SUFFIX,
        )
        for key in placeholder_keys:
            logger.warning("  - %s", key)

    registry = build_default_registry(overrides) if modules is None else modules
    records, state = integrate(model, soil, registry, drivers, events)
    _log_process_caveats(registry, element_configs)

    fingerprint = config_fingerprint(
        input_paths,
        base,
        omitted=(deposition_path,) if deposition_path is not None else (),
        overrides={config_path: model_material(model)},
        extra=(
            {"deposition.series": deposition_material(deposition_series)}
            if deposition_series is not None
            else {}
        ),
    )
    output_directory = _resolve(base, model.output.directory)
    output_paths: dict[str, Path] = {}
    for frequency in model.output.interval:
        output_paths[frequency] = write_table_csv(
            output_directory / TABLE_FILENAMES[frequency],
            tabulate(records, frequency, model.elements, state.layer_ids),
            model.elements,
            state.layer_ids,
            fingerprint=fingerprint,
            frequency=frequency,
            placeholder=resolver.has_placeholders,
        )
    parameters_path = write_parameters_csv(
        output_directory / PARAMETERS_FILENAME,
        resolver.records(),
        fingerprint=fingerprint,
    )
    return RunResult(
        records=records,
        state=state,
        output_paths=output_paths,
        parameters_path=parameters_path,
        config_fingerprint=fingerprint,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hmfms", description="农田重金属通量模型")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="按配置文件运行模型")
    run_parser.add_argument("config", type=Path, help="主配置文件（model.yaml）")
    run_parser.add_argument(
        "--allow-placeholder",
        action="store_true",
        help="占位模式：缺失参数取 0，输出列加 _PLACEHOLDER 后缀（仅供结构演示）",
    )

    validate_parser = sub.add_parser(
        "validate", help="用观测表对比已有模拟输出（不重跑模型）"
    )
    validate_parser.add_argument(
        "config", type=Path, help="对比配置（validation.yaml）"
    )

    demo_parser = sub.add_parser(
        "demo", help="运行十年稻田演示场景"
    )
    demo_parser.add_argument(
        "--no-validate", action="store_true", help="跳过观测对比"
    )

    args = parser.parse_args(argv)

    if args.command == "run":
        result = run_from_config(
            args.config, allow_placeholder=args.allow_placeholder
        )
        last = result.records[-1]
        for frequency, path in result.output_paths.items():
            print(f"输出[{frequency}]: {path}")
        print(f"参数溯源: {result.parameters_path}")
        print(f"配置指纹: {result.config_fingerprint}")
        print(f"天数: {len(result.records)}")
        for element in result.state.elements:
            print(
                f"  {element}: 期末储量 {last.mass[element]} mg/m2；"
                f"累积输入 {last.cumulative_in[element]:.6g} mg/m2；"
                f"累积输出 {last.cumulative_out[element]:.6g} mg/m2；"
                f"闭合误差 {last.closure[element]:.3g} mg/m2"
            )
        return 0

    if args.command == "validate":
        result = run_validation(args.config)
        metrics = result.metrics
        print(f"报告: {result.report_path}")
        print(f"对比图: {result.figure_path}")
        print(f"匹配明细: {result.points_path}")
        print(
            f"匹配数: {metrics.n_pairs}；"
            f"MAE={metrics.mae:.6g}；RMSE={metrics.rmse:.6g} mg/kg"
        )
        return 0

    if args.command == "demo":
        from hmfms.demo import run_demo, summarize

        demo = run_demo(validate=not args.no_validate)
        print(summarize(demo))
        return 0

    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())