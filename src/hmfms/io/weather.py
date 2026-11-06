"""逐日气象输入（降水必需、蒸散可选）。

装载期做四件事：

1. **单位换算**：文件内单位 → mm·d⁻¹（只调用 ``hmfms.utils.units``）。
2. **缺口处理**：按 ``gap_policy`` 报错或线性插值；**绝不静默补零**。
3. **蒸散缺失**：整列缺失时按 0 处理并一次性警告——下渗量因此是上限。
4. **量级检查**：超出配置区间时警告，并提示可能是单位错误。

``INV-1`` 的另一半：本模块只读气象，不参与沉降计算。两者通过
``CombinedDrivers`` 汇合，键名不同，互不覆盖。
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping

from hmfms.config.models import WeatherConfig
from hmfms.core.drivers import DRIVER_ET, DRIVER_PRECIPITATION
from hmfms.utils.units import to_internal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WeatherSeries:
    """逐日降水与蒸散，单位 mm·d⁻¹。"""

    precipitation_mm: Mapping[date, float]
    evapotranspiration_mm: Mapping[date, float]

    def at(self, day: date) -> Mapping[str, Any]:
        return {
            DRIVER_PRECIPITATION: self.precipitation_mm[day],
            DRIVER_ET: self.evapotranspiration_mm[day],
        }


def _read_rows(path: Path) -> tuple[list[str], list[tuple[int, dict[str, str]]]]:
    if not path.is_file():
        raise FileNotFoundError(f"气象文件不存在：{path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"{path} 是空文件")
        rows = [(line_no, row) for line_no, row in enumerate(reader, start=2)]
    return list(reader.fieldnames), rows


def _window(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def _interpolate(points: dict[date, float], day: date) -> float:
    """按天序号线性插值；端点之外不外推（与沉降同一口径）。"""
    known = sorted(points)
    before = [moment for moment in known if moment < day]
    after = [moment for moment in known if moment > day]
    if not before or not after:
        raise ValueError(
            f"气象序列在 {day} 之前或之后没有可用的锚点，无法插值。"
            "首尾缺口只能报错，不能外推。"
        )
    left, right = before[-1], after[0]
    weight = (day - left).days / (right - left).days
    return points[left] + (points[right] - points[left]) * weight


def _number(where: str, row: dict[str, str], column: str) -> float | None:
    """空单元格返回 None，交由 ``gap_policy`` 处理。"""
    text = (row.get(column) or "").strip()
    if text == "":
        return None
    try:
        return float(text)
    except ValueError:
        raise ValueError(f"{where} 列 {column!r} 的值 {text!r} 不是数字") from None


def _fill(
    *,
    points: dict[date, float],
    window: list[date],
    kind: str,
    gap_policy: str,
) -> dict[date, float]:
    missing = [day for day in window if day not in points]
    if missing and gap_policy == "error":
        preview = "、".join(str(day) for day in missing[:5])
        suffix = "" if len(missing) <= 5 else f" 等共 {len(missing)} 天"
        raise ValueError(
            f"气象序列 {kind} 在模拟区间内缺 {len(missing)} 天：{preview}{suffix}"
            "（gap_policy=error）。如需插值请显式设为 gap_policy=interpolate。"
        )
    return {
        day: points[day] if day in points else _interpolate(points, day)
        for day in window
    }


def _warn_if_out_of_range(
    values: Mapping[date, float],
    minimum: float,
    maximum: float,
    kind: str,
) -> None:
    violations = [
        (day, value)
        for day, value in values.items()
        if not (minimum <= value <= maximum)
    ]
    if not violations:
        return
    first_day, first_value = violations[0]
    logger.warning(
        "气象序列 %s 的日值超出配置区间 [%g, %g] mm/day：共 %d 天越界，"
        "首次出现在 %s（%g mm/day）。"
        "请核对单位声明（例如把 cm 或 m 当成 mm）以及数据是否闭合。",
        kind,
        minimum,
        maximum,
        len(violations),
        first_day,
        first_value,
    )


def load_weather(
    path: Path,
    spec: WeatherConfig,
    start: date,
    end: date,
) -> WeatherSeries:
    fields, rows = _read_rows(path)
    missing_columns = sorted(
        {spec.date_column, spec.precipitation_column} - set(fields)
    )
    if missing_columns:
        raise ValueError(
            f"{path} 缺少必需列 {missing_columns}；实际列为 {fields}"
        )

    et_present = spec.evapotranspiration_column in fields
    if not et_present:
        logger.warning(
            "气象文件 %s 没有蒸散列 %r：本次按 0 处理，"
            "下渗量等于降水 + 灌溉，是上限而不是估计值。"
            "若需要扣除蒸散，请补这一列。",
            path.name,
            spec.evapotranspiration_column,
        )

    precipitation: dict[date, float] = {}
    evapotranspiration: dict[date, float] = {}
    seen: dict[date, int] = {}

    for line_no, row in rows:
        where = f"{path.name}:{line_no}"
        text = (row.get(spec.date_column) or "").strip()
        if not text:
            raise ValueError(f"{where} 日期列为空")
        try:
            day = date.fromisoformat(text)
        except ValueError:
            raise ValueError(f"{where} 日期 {text!r} 不是 YYYY-MM-DD 格式") from None
        if day in seen:
            raise ValueError(
                f"{where} 日期 {day} 重复（首次出现在第 {seen[day]} 行）"
            )
        seen[day] = line_no

        raw_precipitation = _number(where, row, spec.precipitation_column)
        if raw_precipitation is not None:
            value = to_internal(
                raw_precipitation, spec.units.precipitation, "water_flux"
            )
            if value < 0.0:
                raise ValueError(f"{where} 降水量不得为负，收到 {value} mm/day")
            precipitation[day] = value

        if not et_present:
            continue
        raw_et = _number(where, row, spec.evapotranspiration_column)
        if raw_et is None:
            continue
        et_value = to_internal(raw_et, spec.units.evapotranspiration, "water_flux")
        if et_value < 0.0:
            raise ValueError(f"{where} 蒸散量不得为负，收到 {et_value} mm/day")
        evapotranspiration[day] = et_value

    window = _window(start, end)
    precipitation_filled = _fill(
        points=precipitation,
        window=window,
        kind="precipitation",
        gap_policy=spec.gap_policy,
    )
    if et_present:
        et_filled = _fill(
            points=evapotranspiration,
            window=window,
            kind="evapotranspiration",
            gap_policy=spec.gap_policy,
        )
    else:
        et_filled = {day: 0.0 for day in window}

    if spec.magnitude_check is not None:
        check = spec.magnitude_check
        if check.precipitation is not None:
            _warn_if_out_of_range(
                precipitation_filled,
                check.precipitation.min,
                check.precipitation.max,
                "precipitation",
            )
        if check.evapotranspiration is not None:
            _warn_if_out_of_range(
                et_filled,
                check.evapotranspiration.min,
                check.evapotranspiration.max,
                "evapotranspiration",
            )

    return WeatherSeries(
        precipitation_mm=precipitation_filled,
        evapotranspiration_mm=et_filled,
    )