"""十年演示场景的一键运行与摘要。

输入随仓库提供（examples/paddy-10y），本模块只负责跑模型、跑观测对比、打印摘要。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hmfms.cli import RunResult, run_from_config
from hmfms.validation import DAYS_PER_YEAR, ValidationResult, run_validation

DEMO_DIR = Path(__file__).resolve().parents[2] / "examples" / "paddy-10y"


@dataclass(frozen=True)
class DemoResult:
    model: RunResult
    validation: ValidationResult | None
    annual_delta: dict[str, float]


def run_demo(base: Path = DEMO_DIR, *, validate: bool = True) -> DemoResult:
    model = run_from_config(base / "model.yaml")
    years = len(model.records) / DAYS_PER_YEAR

    first = model.records[0]
    soil_mass = model.state.soil_mass_kg_m2[1]
    annual_delta = {}
    for element in model.state.elements:
        start_mass = first.mass[element][1] - first.net[element]
        final_mass = model.state.total_mass(element)
        annual_delta[element] = (final_mass - start_mass) / soil_mass / years

    validation = (
        run_validation(base / "validation.yaml") if validate else None
    )
    return DemoResult(
        model=model, validation=validation, annual_delta=annual_delta
    )


def summarize(demo: DemoResult) -> str:
    model = demo.model
    first = model.records[0]
    soil_mass = model.state.soil_mass_kg_m2[1]
    years = len(model.records) / DAYS_PER_YEAR

    lines = [f"演示目录: {model.output_paths['daily'].parent.parent}"]
    for frequency, path in model.output_paths.items():
        lines.append(f"输出[{frequency}]: {path.name}")
    lines.append(f"参数溯源: {model.parameters_path.name}")
    lines.append(f"配置指纹: {model.config_fingerprint}")
    lines.append(f"天数: {len(model.records)}（{years:.2f} 年）")

    for element in model.state.elements:
        start_mass = first.mass[element][1] - first.net[element]
        final_mass = model.state.total_mass(element)
        lines.append(
            f"  {element}: 初始 {start_mass / soil_mass:.6g} → "
            f"期末 {final_mass / soil_mass:.6g} mg/kg；"
            f"年增量 {demo.annual_delta[element]:.6g} mg/kg/a"
        )
    last = model.records[-1]
    for element in model.state.elements:
        lines.append(
            f"  {element} 闭合残差: {last.closure[element]:.3g} mg/m2"
            f"（相对 {last.relative_closure[element]:.3g}）"
        )

    if demo.validation is not None:
        metrics = demo.validation.metrics
        lines.append(f"观测对比报告: {demo.validation.report_path}")
        lines.append(f"对比图: {demo.validation.figure_path}")
        lines.append(
            f"  匹配 {metrics.n_pairs} 点；MAE={metrics.mae:.6g}；"
            f"RMSE={metrics.rmse:.6g} mg/kg"
        )
    return "\n".join(lines)