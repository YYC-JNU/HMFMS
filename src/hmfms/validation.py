"""观测对比：指标、匹配明细、markdown 报告与对比图。

这是**独立于主模型**的一层（ADR-0006）：只读已经生成的模拟 CSV 与用户观测表，
不导入积分器、不读主配置、不触发任何模拟。
"""

from __future__ import annotations

import csv
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from hmfms.config.loader import load_validation_config
from hmfms.config.validation import ValidationConfig
from hmfms.io.observations import (
    MatchedPoint,
    load_observations,
    load_simulation_series,
    match_series,
)

# 年际趋势的时间轴口径：一年按 365.25 天折算（A 类，纯换算定义）。
DAYS_PER_YEAR = 365.25


@dataclass(frozen=True)
class ValidationMetrics:
    n_pairs: int
    mae: float
    rmse: float
    mean_relative_error: float | None
    end_error: float
    end_relative_error: float | None
    trend_sim: float | None
    trend_obs: float | None
    trend_error: float | None
    trend_note: str | None


@dataclass(frozen=True)
class ValidationResult:
    config: ValidationConfig
    matched: list[MatchedPoint]
    metrics: ValidationMetrics
    report_path: Path
    figure_path: Path
    points_path: Path


def _slope(xs: list[float], ys: list[float]) -> float | None:
    count = len(xs)
    mean_x = math.fsum(xs) / count
    mean_y = math.fsum(ys) / count
    denominator = math.fsum((x - mean_x) ** 2 for x in xs)
    if denominator == 0.0:
        return None
    return math.fsum(
        (x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)
    ) / denominator


def compute_metrics(
    matched: list[MatchedPoint], *, trend_min_points: int
) -> ValidationMetrics:
    if not matched:
        raise ValueError("没有任何匹配点，无法计算指标")

    residuals = [point.residual for point in matched]
    count = len(residuals)
    mae = math.fsum(abs(value) for value in residuals) / count
    rmse = math.sqrt(math.fsum(value * value for value in residuals) / count)

    relative = [
        abs(point.residual) / abs(point.obs_value)
        for point in matched
        if point.obs_value != 0.0
    ]
    mean_relative = math.fsum(relative) / len(relative) if relative else None

    last = matched[-1]
    end_error = last.residual
    end_relative = (
        last.residual / last.obs_value if last.obs_value != 0.0 else None
    )

    years = {point.obs_day.year for point in matched}
    if count < trend_min_points or len(years) < 2:
        trend_sim = trend_obs = trend_error = None
        trend_note = (
            "观测点不足以估计年际趋势：需要至少 "
            f"{trend_min_points} 个匹配点且跨至少 2 个日历年"
            f"（当前 {count} 个点、{len(years)} 个年份）。"
            "此时不应把短期误差外推为长期趋势。"
        )
    else:
        origin = matched[0].obs_day
        xs = [
            (point.obs_day - origin).days / DAYS_PER_YEAR for point in matched
        ]
        trend_sim = _slope(xs, [point.sim_value for point in matched])
        trend_obs = _slope(xs, [point.obs_value for point in matched])
        if trend_sim is None or trend_obs is None:
            trend_error = None
            trend_note = "所有观测点在同一时间位置，无法估计趋势斜率。"
        else:
            trend_error = trend_sim - trend_obs
            trend_note = None

    return ValidationMetrics(
        n_pairs=count,
        mae=mae,
        rmse=rmse,
        mean_relative_error=mean_relative,
        end_error=end_error,
        end_relative_error=end_relative,
        trend_sim=trend_sim,
        trend_obs=trend_obs,
        trend_error=trend_error,
        trend_note=trend_note,
    )


def _meta(path: Path) -> dict[str, str]:
    first = path.read_text(encoding="utf-8-sig").splitlines()[0]
    if not first.startswith("#"):
        return {}
    tokens: dict[str, str] = {}
    for token in first.lstrip("#").split():
        key, _, value = token.partition("=")
        tokens[key] = value
    return tokens


def _number(value: float | None, digits: int = 6) -> str:
    return "—" if value is None else f"{value:.{digits}g}"


def _percent(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.3f}%"


def write_points(path: Path, matched: list[MatchedPoint]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = (
        "obs_date",
        "obs_value_mg_kg",
        "sim_value_mg_kg",
        "residual_mg_kg",
        "method",
        "sim_date",
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for point in matched:
            writer.writerow(
                [
                    point.obs_day.isoformat(),
                    f"{point.obs_value:.12g}",
                    f"{point.sim_value:.12g}",
                    f"{point.residual:.12g}",
                    point.method,
                    point.sim_date,
                ]
            )
    return path


def write_report(
    path: Path,
    *,
    config: ValidationConfig,
    simulation_path: Path,
    observation_path: Path,
    matched: list[MatchedPoint],
    metrics: ValidationMetrics,
    figure_name: str,
) -> Path:
    meta = _meta(simulation_path)
    methods = Counter(point.method for point in matched)
    method_text = "、".join(
        f"{name} {methods.get(name, 0)}"
        for name in ("exact", "nearest", "linear")
    )
    schema = meta.get("schema_version", "未知")
    fingerprint = meta.get("fingerprint", "未知")

    lines = [
        "# 观测对比报告",
        "",
        f"- 模拟输出：`{simulation_path.name}`"
        f"（schema_version={schema}，fingerprint={fingerprint}）",
        f"- 观测表：`{observation_path.name}`",
        f"- 观测单位：{config.observations.units}（已换算为 mg/kg）",
        f"- 元素 / 土层：{config.element} / L{config.layer_id}",
        f"- 匹配规则：{config.match.rule}"
        f"（容差 {config.match.tolerance_days} 天）",
        f"- 匹配点数：{metrics.n_pairs}（{method_text}）",
        f"- 对比图：`{figure_name}`",
        "",
        "## 误差指标",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| MAE | {_number(metrics.mae)} mg/kg |",
        f"| RMSE | {_number(metrics.rmse)} mg/kg |",
        f"| 平均相对误差 | {_percent(metrics.mean_relative_error)} |",
        f"| 期末误差（最后一个匹配点，模拟 − 观测） | {_number(metrics.end_error)} mg/kg |",
        f"| 期末相对误差 | {_percent(metrics.end_relative_error)} |",
        f"| 年际趋势（模拟） | {_number(metrics.trend_sim)} mg/kg/a |",
        f"| 年际趋势（观测） | {_number(metrics.trend_obs)} mg/kg/a |",
        f"| 年际趋势误差（模拟 − 观测） | {_number(metrics.trend_error)} mg/kg/a |",
        "",
        "## 不确定性说明",
        "",
    ]

    if metrics.trend_note:
        lines.append(f"- {metrics.trend_note}")
    nonzero = sum(1 for point in matched if point.obs_value != 0.0)
    lines.append(
        f"- 相对误差只统计观测值非零的匹配点（{nonzero}/{metrics.n_pairs}）。"
    )
    if metrics.trend_sim is not None:
        lines.append(
            "- 趋势为普通最小二乘斜率，未提供置信区间；"
            "观测点少时斜率对个别点敏感。"
        )
    lines.append(
        "- 本报告只做误差记账，是否可接受由使用者结合观测口径与不确定性判断。"
    )
    lines.append(
        "- 观测对比在独立命令中完成，主模型运行时未读取任何观测数据。"
    )
    lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_figure(
    path: Path,
    *,
    element: str,
    layer_id: int,
    matched: list[MatchedPoint],
    metrics: ValidationMetrics,
) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    days = [point.obs_day for point in matched]
    observed = [point.obs_value for point in matched]
    simulated = [point.sim_value for point in matched]

    figure, (series_axis, scatter_axis) = plt.subplots(1, 2, figsize=(10, 4))

    series_axis.plot(days, simulated, "-o", markersize=3, label="simulated")
    series_axis.plot(days, observed, "x", label="observed")
    series_axis.set_title(f"{element} L{layer_id}")
    series_axis.set_xlabel("date")
    series_axis.set_ylabel("concentration (mg/kg)")
    series_axis.legend()

    scatter_axis.scatter(observed, simulated, s=18)
    low = min(observed + simulated)
    high = max(observed + simulated)
    if high == low:
        high = low + 1.0
    scatter_axis.plot([low, high], [low, high], "--", linewidth=1, color="grey")
    scatter_axis.set_xlabel("observed (mg/kg)")
    scatter_axis.set_ylabel("simulated (mg/kg)")
    scatter_axis.set_title(f"MAE={metrics.mae:.3g}  RMSE={metrics.rmse:.3g}")

    figure.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=150)
    plt.close(figure)
    return path


def _resolve(base: Path, candidate: Path) -> Path:
    return candidate if candidate.is_absolute() else base / candidate


def run_validation(config_path: Path) -> ValidationResult:
    """执行一次观测对比。只读已有 CSV，不运行模型。"""
    config_path = Path(config_path).resolve()
    config = load_validation_config(config_path)
    base = config_path.parent

    simulation_path = _resolve(base, config.simulation.file)
    observation_path = _resolve(base, config.observations.file)
    output_directory = _resolve(base, config.output.directory)

    simulation = load_simulation_series(
        simulation_path,
        value_column=config.simulation_value_column,
        date_column=config.simulation.date_column,
    )
    observations = load_observations(observation_path, config.observations)
    matched = match_series(simulation, observations, config.match)
    metrics = compute_metrics(
        matched, trend_min_points=config.metrics.trend_min_points
    )

    protected = {simulation_path.resolve(), observation_path.resolve()}
    for output_name in (
        config.output.points_name,
        config.output.figure_name,
        config.output.report_name,
    ):
        output = output_directory / output_name
        if output.resolve() in protected:
            raise ValueError(
                f"对比产物 {output} 会覆盖输入文件；"
                "请把 output.directory 指到独立目录，或改输出文件名。"
            )

    points_path = write_points(
        output_directory / config.output.points_name, matched
    )
    figure_path = write_figure(
        output_directory / config.output.figure_name,
        element=config.element,
        layer_id=config.layer_id,
        matched=matched,
        metrics=metrics,
    )
    report_path = write_report(
        output_directory / config.output.report_name,
        config=config,
        simulation_path=simulation_path,
        observation_path=observation_path,
        matched=matched,
        metrics=metrics,
        figure_name=config.output.figure_name,
    )
    return ValidationResult(
        config=config,
        matched=matched,
        metrics=metrics,
        report_path=report_path,
        figure_path=figure_path,
        points_path=points_path,
    )