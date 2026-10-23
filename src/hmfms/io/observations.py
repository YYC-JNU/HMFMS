"""观测序列与模拟序列的装载、日期匹配。

本模块只在 validate 命令里被使用；主模型从不导入它（ADR-0006）。
"""

from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from hmfms.config.validation import MatchConfig, ObservationSeriesConfig
from hmfms.utils.units import to_internal


@dataclass(frozen=True)
class SeriesPoint:
    day: date
    value: float


@dataclass(frozen=True)
class MatchedPoint:
    obs_day: date
    obs_value: float
    sim_value: float
    method: str
    sim_date: str

    @property
    def residual(self) -> float:
        return self.sim_value - self.obs_value


def _data_rows(path: Path) -> tuple[list[str], list[tuple[int, dict[str, str]]]]:
    """读 CSV，跳过以 ``#`` 开头的元信息行（模拟输出首行）。"""
    if not path.is_file():
        raise FileNotFoundError(f"文件不存在：{path}")
    text = path.read_text(encoding="utf-8-sig")
    raw_lines = text.splitlines()
    kept = [
        (line_no, line)
        for line_no, line in enumerate(raw_lines, start=1)
        if line.strip() and not line.startswith("#")
    ]
    reader = csv.DictReader(io.StringIO("\n".join(line for _, line in kept)))
    fields = list(reader.fieldnames or [])
    rows = [(kept[index][0], row) for index, row in enumerate(reader)]
    return fields, rows


def _require_columns(path: Path, fields: list[str], needed: list[str]) -> None:
    missing = sorted({name for name in needed if name not in fields})
    if missing:
        raise ValueError(f"{path} 缺少必需列 {missing}；实际列 {fields}")


def _parse_date(where: str, text: str) -> date:
    stripped = text.strip()
    if not stripped:
        raise ValueError(f"{where} 日期列为空")
    try:
        return date.fromisoformat(stripped)
    except ValueError:
        raise ValueError(f"{where} 日期 {stripped!r} 不是 YYYY-MM-DD 格式") from None


def _parse_value(where: str, text: str) -> float:
    stripped = text.strip()
    if stripped == "":
        raise ValueError(f"{where} 数值列为空")
    try:
        value = float(stripped)
    except ValueError:
        raise ValueError(f"{where} 数值 {stripped!r} 不是数字") from None
    if not math.isfinite(value):
        raise ValueError(f"{where} 数值 {stripped!r} 不是有限数")
    return value


def load_simulation_series(
    path: Path,
    *,
    value_column: str,
    date_column: str,
) -> list[SeriesPoint]:
    fields, rows = _data_rows(path)
    _require_columns(path, fields, [date_column, value_column])

    points: list[SeriesPoint] = []
    seen: dict[date, int] = {}
    for line_no, row in rows:
        where = f"{path.name}:{line_no}"
        day = _parse_date(where, row[date_column])
        if day in seen:
            raise ValueError(
                f"{where} 模拟日期 {day} 重复（首次出现在第 {seen[day]} 行）"
            )
        seen[day] = line_no
        value = _parse_value(where, row[value_column])
        if value < 0.0:
            raise ValueError(f"{where} 模拟值为负 {value}；含量不得为负")
        points.append(SeriesPoint(day=day, value=value))

    points.sort(key=lambda point: point.day)
    return points


def load_observations(
    path: Path, spec: ObservationSeriesConfig
) -> list[SeriesPoint]:
    fields, rows = _data_rows(path)
    _require_columns(path, fields, [spec.date_column, spec.value_column])

    points: list[SeriesPoint] = []
    seen: dict[date, int] = {}
    for line_no, row in rows:
        where = f"{path.name}:{line_no}"
        day = _parse_date(where, row[spec.date_column])
        if day in seen:
            raise ValueError(
                f"{where} 观测日期 {day} 重复（首次出现在第 {seen[day]} 行）；"
                "同一天的多条记录请先合并。"
            )
        seen[day] = line_no
        value = to_internal(
            _parse_value(where, row[spec.value_column]),
            spec.units,
            "concentration",
        )
        if value < 0.0:
            raise ValueError(f"{where} 观测值为负 {value}；含量不得为负")
        points.append(SeriesPoint(day=day, value=value))

    points.sort(key=lambda point: point.day)
    return points


def match_series(
    simulation: list[SeriesPoint],
    observations: list[SeriesPoint],
    spec: MatchConfig,
) -> list[MatchedPoint]:
    """按声明规则把观测日期对到模拟日期上，逐点记录匹配方式。"""
    if not simulation:
        raise ValueError("模拟序列为空，无法对比")
    if not observations:
        raise ValueError("观测序列为空，无法对比")

    by_day = {point.day: point for point in simulation}
    days = [point.day for point in simulation]

    matched: list[MatchedPoint] = []
    for obs in observations:
        exact = by_day.get(obs.day)
        if exact is not None:
            matched.append(
                MatchedPoint(
                    obs_day=obs.day,
                    obs_value=obs.value,
                    sim_value=exact.value,
                    method="exact",
                    sim_date=exact.day.isoformat(),
                )
            )
            continue

        if spec.rule == "exact":
            raise ValueError(
                f"观测日期 {obs.day} 在模拟序列中不存在（rule: exact）。"
                "如需插值或就近匹配，请把 match.rule 改为 linear 或 nearest。"
            )

        if spec.rule == "nearest":
            nearest = min(days, key=lambda day: (abs((day - obs.day).days), day))
            distance = abs((nearest - obs.day).days)
            if distance > spec.tolerance_days:
                raise ValueError(
                    f"观测日期 {obs.day} 与最近模拟日期 {nearest} 相距 "
                    f"{distance} 天，超过容差 {spec.tolerance_days} 天。"
                )
            matched.append(
                MatchedPoint(
                    obs_day=obs.day,
                    obs_value=obs.value,
                    sim_value=by_day[nearest].value,
                    method="nearest",
                    sim_date=nearest.isoformat(),
                )
            )
            continue

        left = max((day for day in days if day < obs.day), default=None)
        right = min((day for day in days if day > obs.day), default=None)
        if left is None or right is None:
            raise ValueError(
                f"观测日期 {obs.day} 超出模拟序列范围 "
                f"[{days[0]}, {days[-1]}]（rule: linear），无法插值。"
            )
        span = (right - left).days
        if span > spec.tolerance_days:
            raise ValueError(
                f"观测日期 {obs.day} 的插值区间 {left}~{right} 跨 {span} 天，"
                f"超过容差 {spec.tolerance_days} 天。"
            )
        weight = (obs.day - left).days / span
        value = by_day[left].value + (
            by_day[right].value - by_day[left].value
        ) * weight
        matched.append(
            MatchedPoint(
                obs_day=obs.day,
                obs_value=obs.value,
                sim_value=value,
                method="linear",
                sim_date=f"{left}~{right}",
            )
        )

    matched.sort(key=lambda point: point.obs_day)
    return matched