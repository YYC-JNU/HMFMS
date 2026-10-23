"""沉降驱动数据。

把沉降 CSV 读成逐日序列，并做三件事：

1. **单位换算**：文件内单位 → mg·m⁻²·d⁻¹（只调用 ``hmfms.utils.units``）。
2. **缺口处理**：按配置报错或线性插值，**绝不静默补零**。
3. **量级检查**：超出配置区间时警告，并提示可能是单位错误。

``INV-1``：本模块只读沉降文件，不读取降雨，也不由降雨推导任何通量。
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping

from hmfms.config.models import DepositionConfig
from hmfms.core.drivers import DRIVER_DEPOSITION_DRY, DRIVER_DEPOSITION_WET
from hmfms.utils.units import to_internal

logger = logging.getLogger(__name__)

DRIVER_DRY = DRIVER_DEPOSITION_DRY
DRIVER_WET = DRIVER_DEPOSITION_WET


@dataclass(frozen=True)
class DepositionSeries:
    """逐日干/湿沉降通量，单位 mg·m⁻²·d⁻¹。"""

    dry: dict[str, dict[date, float]]
    wet: dict[str, dict[date, float]]

    @property
    def elements(self) -> tuple[str, ...]:
        return tuple(self.dry)

    def at(self, day: date) -> Mapping[str, Any]:
        """当日驱动，供 ``StepContext.drivers`` 使用。"""
        return {
            DRIVER_DRY: {e: self.dry[e][day] for e in self.dry},
            DRIVER_WET: {e: self.wet[e][day] for e in self.wet},
        }


def _read_rows(path: Path) -> tuple[list[str], list[tuple[int, dict[str, str]]]]:
    if not path.is_file():
        raise FileNotFoundError(f"沉降文件不存在：{path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"{path} 是空文件")
        rows = [(line_no, row) for line_no, row in enumerate(reader, start=2)]
    return list(reader.fieldnames), rows


def _require_columns(path: Path, fields: list[str], needed: set[str]) -> None:
    missing = sorted(needed - set(fields))
    if missing:
        raise ValueError(f"{path} 缺少必需列 {missing}；实际列为 {fields}")


def _date_range(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def _interpolate(points: dict[date, float], day: date) -> float:
    """按天序号做线性插值；端点之外不外推。"""
    known = sorted(points)
    before = [d for d in known if d < day]
    after = [d for d in known if d > day]
    if not before or not after:
        raise ValueError(
            f"沉降序列在 {day} 之前或之后没有可用的锚点，无法插值。"
            "首尾缺口只能报错，不能外推。"
        )
    left, right = before[-1], after[0]
    span = (right - left).days
    weight = (day - left).days / span
    return points[left] + (points[right] - points[left]) * weight


def _value(where: str, row: dict[str, str], column: str) -> float:
    text = row[column].strip()
    if text == "":
        raise ValueError(
            f"{where} 列 {column!r} 为空；缺口应交由 gap_policy 处理，不得静默补零"
        )
    try:
        return float(text)
    except ValueError:
        raise ValueError(f"{where} 列 {column!r} 的值 {text!r} 不是数字") from None


def load_deposition(
    path: Path,
    spec: DepositionConfig,
    elements: tuple[str, ...],
    start: date,
    end: date,
) -> DepositionSeries:
    fields, rows = _read_rows(path)
    needed = {spec.date_column}
    needed.update(spec.dry_columns.values())
    needed.update(spec.wet_columns.values())
    _require_columns(path, fields, needed)

    raw: dict[str, dict[date, float]] = {
        f"{kind}:{element}": {}
        for element in elements
        for kind in ("dry", "wet")
    }

    seen: dict[date, int] = {}
    for line_no, row in rows:
        where = f"{path}:{line_no}"
        text = row[spec.date_column].strip()
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

        for element in elements:
            for kind, mapping in (("dry", spec.dry_columns), ("wet", spec.wet_columns)):
                column = mapping[element]
                flux = to_internal(_value(where, row, column), spec.units, "flux")
                if flux < 0.0:
                    raise ValueError(
                        f"{where} 列 {column!r} 为负值 {flux}；沉降通量不得为负"
                    )
                raw[f"{kind}:{element}"][day] = flux

    window = _date_range(start, end)
    dry: dict[str, dict[date, float]] = {element: {} for element in elements}
    wet: dict[str, dict[date, float]] = {element: {} for element in elements}

    for element in elements:
        for kind, target in (("dry", dry), ("wet", wet)):
            points = raw[f"{kind}:{element}"]
            missing = [day for day in window if day not in points]
            if missing and spec.gap_policy == "error":
                preview = "、".join(str(day) for day in missing[:5])
                suffix = "" if len(missing) <= 5 else f" 等共 {len(missing)} 天"
                raise ValueError(
                    f"沉降序列 {kind}/{element} 在模拟区间内缺 {len(missing)} 天："
                    f"{preview}{suffix}（gap_policy=error）。"
                    "如需插值请显式设为 gap_policy=interpolate。"
                )
            for day in window:
                target[element][day] = (
                    points[day] if day in points else _interpolate(points, day)
                )

    if spec.magnitude_check:
        for element, limit in spec.magnitude_check.items():
            totals = {
                day: dry[element][day] + wet[element][day] for day in window
            }
            violations = [
                (day, value)
                for day, value in totals.items()
                if not (limit.min <= value <= limit.max)
            ]
            if violations:
                first_day, first_value = violations[0]
                logger.warning(
                    "%s 的日沉降通量超出配置区间 [%g, %g] mg/m2/d：共 %d 天越界，"
                    "首次出现在 %s（%g mg/m2/d）。"
                    "请核对单位声明（例如 μg 与 mg 混淆）以及区域是否匹配。",
                    element,
                    limit.min,
                    limit.max,
                    len(violations),
                    first_day,
                    first_value,
                )

    return DepositionSeries(dry=dry, wet=wet)