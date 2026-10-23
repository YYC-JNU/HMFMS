"""施肥事件表装载。

装载期完成三件事：

1. **单位归一**：施用量 → kg·ha⁻¹，浓度 → mg·kg⁻¹。
2. **集中解析**：每个事件的浓度经五级解析链取值——事件行覆盖（一级）
   优先于库条目（三/四级）。解析只做一次，结果随事件带走。
3. **引用完整性**：``fertilizer_id`` 取不到含量时报错，并指出文件与行号。

模块本身只做 ``施用量 × 含量 ÷ 10000``，不碰配置（ADR-0010）。
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from hmfms.config.models import (
    FertilizerEventConfig,
    HarvestEventConfig,
    IrrigationEventConfig,
    StrawEventConfig,
)
from hmfms.core.errors import DuplicateEventError
from hmfms.core.events import (
    EVENTS_FERTILIZER,
    EVENTS_HARVEST,
    EVENTS_IRRIGATION,
    EVENTS_STRAW,
)
from hmfms.parameters.resolver import ParameterRequest, ParameterResolver
from hmfms.utils.units import mass_per_area_from_rate, to_internal

logger = logging.getLogger(__name__)

FERTILIZER_DOC = "docs/parameters.md §1–2（肥料重金属含量）"
REQUIRED_COLUMNS = ("fertilizer_id", "amount")


@dataclass(frozen=True)
class FertilizerEvent:
    event_id: str
    day: date
    fertilizer_id: str
    amount_kg_ha: float
    concentration_mg_kg: dict[str, float]
    source: str


@dataclass(frozen=True)
class FertilizerSchedule:
    by_day: Mapping[date, tuple[FertilizerEvent, ...]]

    def at(self, day: date) -> Mapping[str, Any]:
        return {EVENTS_FERTILIZER: self.by_day.get(day, ())}


IRRIGATION_DOC = "docs/parameters.md §1–2（灌溉水浓度）"
IRRIGATION_REQUIRED = ("amount", "water_source")


@dataclass(frozen=True)
class IrrigationEvent:
    event_id: str
    day: date
    water_source: str
    amount_mm: float
    concentration_mg_l: dict[str, float]
    source: str


@dataclass(frozen=True)
class IrrigationSchedule:
    by_day: Mapping[date, tuple[IrrigationEvent, ...]]

    def at(self, day: date) -> Mapping[str, Any]:
        return {EVENTS_IRRIGATION: self.by_day.get(day, ())}


def irrigation_key(water_source: str, element: str) -> str:
    """参数库中的键：``irr.<水源>.<元素>``。"""
    return f"irr.{water_source}.{element}"


def load_irrigation_events(
    path: Path,
    spec: IrrigationEventConfig,
    elements: tuple[str, ...],
    resolver: ParameterResolver,
    start: date,
    end: date,
) -> IrrigationSchedule:
    if not path.is_file():
        raise FileNotFoundError(f"灌溉事件表不存在：{path}")

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = [(line_no, row) for line_no, row in enumerate(reader, start=2)]

    missing = sorted({spec.date_column, *IRRIGATION_REQUIRED} - set(fields))
    if missing:
        raise ValueError(f"{path} 缺少必需列 {missing}；实际列为 {fields}")

    by_day: dict[date, list[IrrigationEvent]] = {}

    for line_no, row in rows:
        where = f"{path.name}:{line_no}"
        text = (row.get(spec.date_column) or "").strip()
        if not text:
            raise ValueError(f"{where} 日期列为空")
        try:
            day = date.fromisoformat(text)
        except ValueError:
            raise ValueError(f"{where} 日期 {text!r} 不是 YYYY-MM-DD 格式") from None

        if day < start or day > end:
            logger.warning(
                "%s 的日期 %s 落在模拟区间 [%s, %s] 之外，已忽略。",
                where,
                day,
                start,
                end,
            )
            continue

        water_source = (row.get("water_source") or "").strip()
        if not water_source:
            raise ValueError(f"{where} water_source 为空")

        amount_mm = to_internal(
            _number(where, row, "amount"), spec.units.amount, "water_depth"
        )
        if amount_mm < 0.0:
            raise ValueError(f"{where} 灌溉量为负 {amount_mm} mm")

        concentration: dict[str, float] = {}
        for element in elements:
            override_column = f"{element}_mg_L_override"
            override = (
                _cell(row, override_column) if override_column in fields else None
            )
            concentration[element] = resolver.resolve(
                ParameterRequest(
                    key=irrigation_key(water_source, element),
                    unit=spec.units.concentration,
                    dimension="water_concentration",
                    event=override,
                    event_source=where,
                    selected=irrigation_key(water_source, element),
                    doc_section=IRRIGATION_DOC,
                    context=f"{where} 水源 {water_source!r} 的 {element} 浓度",
                    suggestions=(
                        f"在事件行填入 {override_column} 覆盖值",
                        f"在参数库中新增键 {irrigation_key(water_source, element)!r} 并写明来源",
                    ),
                )
            )

        by_day.setdefault(day, []).append(
            IrrigationEvent(
                event_id=(row.get("event_id") or "").strip() or where,
                day=day,
                water_source=water_source,
                amount_mm=amount_mm,
                concentration_mg_l=concentration,
                source=where,
            )
        )

    # 同日多条保留原始顺序（按行号），以便结果可复现
    frozen = {day: tuple(events) for day, events in by_day.items()}
    return IrrigationSchedule(by_day=frozen)


HARVEST_DOC = "docs/parameters.md §1–2（作物器官含量）"
HARVEST_REQUIRED = ("crop_id", "organ", "yield", "removal_fraction")


@dataclass(frozen=True)
class HarvestEvent:
    event_id: str
    day: date
    crop_id: str
    organ: str
    yield_kg_ha: float
    removal_fraction: float
    concentration_mg_kg: dict[str, float]
    source: str


@dataclass(frozen=True)
class HarvestSchedule:
    by_day: Mapping[date, tuple[HarvestEvent, ...]]

    def at(self, day: date) -> Mapping[str, Any]:
        return {EVENTS_HARVEST: self.by_day.get(day, ())}


def crop_key(crop_id: str, organ: str, element: str) -> str:
    """参数库中的键：``crop.<作物>.<器官>.<元素>``。"""
    return f"crop.{crop_id}.{organ}.{element}"


def load_harvest_events(
    path: Path,
    spec: HarvestEventConfig,
    elements: tuple[str, ...],
    resolver: ParameterResolver,
    start: date,
    end: date,
) -> HarvestSchedule:
    if not path.is_file():
        raise FileNotFoundError(f"收获事件表不存在：{path}")

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = [(line_no, row) for line_no, row in enumerate(reader, start=2)]

    missing = sorted({spec.date_column, *HARVEST_REQUIRED} - set(fields))
    if missing:
        raise ValueError(f"{path} 缺少必需列 {missing}；实际列为 {fields}")

    by_day: dict[date, list[HarvestEvent]] = {}
    seen: dict[tuple[date, str, str], int] = {}

    for line_no, row in rows:
        where = f"{path.name}:{line_no}"
        text = (row.get(spec.date_column) or "").strip()
        if not text:
            raise ValueError(f"{where} 日期列为空")
        try:
            day = date.fromisoformat(text)
        except ValueError:
            raise ValueError(f"{where} 日期 {text!r} 不是 YYYY-MM-DD 格式") from None

        crop_id = (row.get("crop_id") or "").strip()
        organ = (row.get("organ") or "").strip()
        if not crop_id:
            raise ValueError(f"{where} crop_id 为空")
        if not organ:
            raise ValueError(f"{where} organ 为空")

        if day < start or day > end:
            logger.warning(
                "%s 的日期 %s 落在模拟区间 [%s, %s] 之外，已忽略。",
                where,
                day,
                start,
                end,
            )
            continue

        key = (day, crop_id, organ)
        if key in seen:
            raise DuplicateEventError(
                kind="收获",
                path=str(path),
                day=day,
                detail=f"{crop_id} / {organ}",
                first_line=seen[key],
                second_line=line_no,
                suggestion=(
                    "单季同一器官只能收获一次；若确为两季，请用不同的 crop_id 区分"
                ),
            )
        seen[key] = line_no

        yield_kg_ha = to_internal(
            _number(where, row, "yield"), spec.units.yield_rate, "application_rate"
        )
        if yield_kg_ha < 0.0:
            raise ValueError(f"{where} 产量为负 {yield_kg_ha} kg/ha")

        removal = _number(where, row, "removal_fraction")
        if not 0.0 <= removal <= 1.0:
            raise ValueError(f"{where} removal_fraction 应在 [0, 1]，收到 {removal}")

        concentration: dict[str, float] = {}
        for element in elements:
            override_column = f"{element}_mg_kg_override"
            override = (
                _cell(row, override_column) if override_column in fields else None
            )
            concentration[element] = resolver.resolve(
                ParameterRequest(
                    key=crop_key(crop_id, organ, element),
                    unit=spec.units.concentration,
                    dimension="concentration",
                    event=override,
                    event_source=where,
                    selected=crop_key(crop_id, organ, element),
                    doc_section=HARVEST_DOC,
                    context=f"{where} 作物 {crop_id!r} 器官 {organ!r} 的 {element} 含量",
                    suggestions=(
                        f"在事件行填入 {override_column} 覆盖值",
                        f"在参数库中新增键 {crop_key(crop_id, organ, element)!r} 并写明来源",
                    ),
                )
            )

        by_day.setdefault(day, []).append(
            HarvestEvent(
                event_id=(row.get("event_id") or "").strip() or where,
                day=day,
                crop_id=crop_id,
                organ=organ,
                yield_kg_ha=yield_kg_ha,
                removal_fraction=removal,
                concentration_mg_kg=concentration,
                source=where,
            )
        )

    frozen = {
        day: tuple(sorted(events, key=lambda e: (e.crop_id, e.organ, e.source)))
        for day, events in by_day.items()
    }
    return HarvestSchedule(by_day=frozen)


def fertilizer_key(fertilizer_id: str, element: str) -> str:
    """参数库中的键：``fert.<品类>.<元素>``。"""
    return f"fert.{fertilizer_id}.{element}"


def _number(where: str, row: dict[str, str], column: str) -> float:
    text = (row.get(column) or "").strip()
    if not text:
        raise ValueError(f"{where} 列 {column!r} 为空")
    try:
        return float(text)
    except ValueError:
        raise ValueError(f"{where} 列 {column!r} 的值 {text!r} 不是数字") from None


def _cell(row: dict[str, str], column: str) -> float | None:
    text = (row.get(column) or "").strip()
    return float(text) if text else None


STRAW_DOC = "docs/parameters.md §1–2（作物器官含量）"
STRAW_REQUIRED = ("crop_id", "organ", "amount", "return_fraction")


@dataclass(frozen=True)
class StrawEvent:
    event_id: str
    day: date
    crop_id: str
    organ: str
    mass_mg_m2: dict[str, float]
    return_fraction: float
    source: str


@dataclass(frozen=True)
class StrawSchedule:
    by_day: Mapping[date, tuple[StrawEvent, ...]]

    def at(self, day: date) -> Mapping[str, Any]:
        return {EVENTS_STRAW: self.by_day.get(day, ())}


def load_straw_events(
    path: Path,
    spec: StrawEventConfig,
    elements: tuple[str, ...],
    resolver: ParameterResolver,
    start: date,
    end: date,
) -> StrawSchedule:
    if not path.is_file():
        raise FileNotFoundError(f"秸秆事件表不存在：{path}")

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = [(line_no, row) for line_no, row in enumerate(reader, start=2)]

    missing = sorted({spec.date_column, *STRAW_REQUIRED} - set(fields))
    if missing:
        raise ValueError(f"{path} 缺少必需列 {missing}；实际列为 {fields}")

    by_day: dict[date, list[StrawEvent]] = {}
    seen: dict[tuple[date, str, str], int] = {}

    for line_no, row in rows:
        where = f"{path.name}:{line_no}"
        text = (row.get(spec.date_column) or "").strip()
        if not text:
            raise ValueError(f"{where} 日期列为空")
        try:
            day = date.fromisoformat(text)
        except ValueError:
            raise ValueError(f"{where} 日期 {text!r} 不是 YYYY-MM-DD 格式") from None

        crop_id = (row.get("crop_id") or "").strip()
        organ = (row.get("organ") or "").strip()
        if not crop_id:
            raise ValueError(f"{where} crop_id 为空")
        if not organ:
            raise ValueError(f"{where} organ 为空")

        if day < start or day > end:
            logger.warning(
                "%s 的日期 %s 落在模拟区间 [%s, %s] 之外，已忽略。",
                where,
                day,
                start,
                end,
            )
            continue

        key = (day, crop_id, organ)
        if key in seen:
            raise DuplicateEventError(
                kind="秸秆",
                path=str(path),
                day=day,
                detail=f"{crop_id} / {organ}",
                first_line=seen[key],
                second_line=line_no,
                suggestion="同一天的同一作物器官只能有一条秸秆事件",
            )
        seen[key] = line_no

        amount_kg_ha = to_internal(
            _number(where, row, "amount"), spec.units.amount, "application_rate"
        )
        if amount_kg_ha < 0.0:
            raise ValueError(f"{where} 秸秆量为负 {amount_kg_ha} kg/ha")

        fraction = _number(where, row, "return_fraction")
        if not 0.0 <= fraction <= 1.0:
            raise ValueError(f"{where} return_fraction 应在 [0, 1]，收到 {fraction}")

        mass: dict[str, float] = {}
        for element in elements:
            override_column = f"{element}_mg_kg_override"
            override = (
                _cell(row, override_column) if override_column in fields else None
            )
            concentration = resolver.resolve(
                ParameterRequest(
                    key=crop_key(crop_id, organ, element),
                    unit=spec.units.concentration,
                    dimension="concentration",
                    event=override,
                    event_source=where,
                    selected=crop_key(crop_id, organ, element),
                    doc_section=STRAW_DOC,
                    context=f"{where} 作物 {crop_id!r} 器官 {organ!r} 的 {element} 含量",
                    suggestions=(
                        f"在事件行填入 {override_column} 覆盖值",
                        f"在参数库中新增键 {crop_key(crop_id, organ, element)!r} 并写明来源",
                    ),
                )
            )
            mass[element] = mass_per_area_from_rate(amount_kg_ha, concentration)

        by_day.setdefault(day, []).append(
            StrawEvent(
                event_id=(row.get("event_id") or "").strip() or where,
                day=day,
                crop_id=crop_id,
                organ=organ,
                mass_mg_m2=mass,
                return_fraction=fraction,
                source=where,
            )
        )

    frozen = {
        day: tuple(sorted(events, key=lambda e: (e.crop_id, e.organ, e.source)))
        for day, events in by_day.items()
    }
    return StrawSchedule(by_day=frozen)


def load_fertilizer_events(
    path: Path,
    spec: FertilizerEventConfig,
    elements: tuple[str, ...],
    resolver: ParameterResolver,
    start: date,
    end: date,
) -> FertilizerSchedule:
    if not path.is_file():
        raise FileNotFoundError(f"施肥事件表不存在：{path}")

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = [(line_no, row) for line_no, row in enumerate(reader, start=2)]

    missing = sorted({spec.date_column, *REQUIRED_COLUMNS} - set(fields))
    if missing:
        raise ValueError(f"{path} 缺少必需列 {missing}；实际列为 {fields}")

    by_day: dict[date, list[FertilizerEvent]] = {}
    seen: dict[tuple[date, str], int] = {}

    for line_no, row in rows:
        where = f"{path.name}:{line_no}"
        text = (row.get(spec.date_column) or "").strip()
        if not text:
            raise ValueError(f"{where} 日期列为空")
        try:
            day = date.fromisoformat(text)
        except ValueError:
            raise ValueError(f"{where} 日期 {text!r} 不是 YYYY-MM-DD 格式") from None

        fertilizer_id = (row.get("fertilizer_id") or "").strip()
        if not fertilizer_id:
            raise ValueError(f"{where} fertilizer_id 为空")

        if day < start or day > end:
            logger.warning(
                "%s 的日期 %s 落在模拟区间 [%s, %s] 之外，已忽略。",
                where,
                day,
                start,
                end,
            )
            continue

        amount = to_internal(
            float(row["amount"]), spec.units.amount, "application_rate"
        )
        if amount < 0.0:
            raise ValueError(f"{where} 施用量为负 {amount} kg/ha")

        concentration: dict[str, float] = {}
        for element in elements:
            override_column = f"{element}_mg_kg_override"
            override = (
                _cell(row, override_column) if override_column in fields else None
            )
            concentration[element] = resolver.resolve(
                ParameterRequest(
                    key=fertilizer_key(fertilizer_id, element),
                    unit=spec.units.concentration,
                    dimension="concentration",
                    event=override,
                    event_source=where,
                    # 事件行点名了品类，即"用户选择了库条目" → 三级
                    selected=fertilizer_key(fertilizer_id, element),
                    doc_section=FERTILIZER_DOC,
                    context=f"{where} 品类 {fertilizer_id!r} 的 {element} 含量",
                    suggestions=(
                        f"在事件行填入 {override_column} 覆盖值",
                        f"在参数库中新增键 {fertilizer_key(fertilizer_id, element)!r} 并写明来源",
                    ),
                )
            )

        key = (day, fertilizer_id)
        if key in seen:
            logger.warning(
                "%s 与第 %d 行是同一天同一品类的施肥事件（%s, %s），已累加。"
                "如果是重复录入，请删除其中一条。",
                where,
                seen[key],
                day,
                fertilizer_id,
            )
        else:
            seen[key] = line_no

        by_day.setdefault(day, []).append(
            FertilizerEvent(
                event_id=(row.get("event_id") or "").strip() or where,
                day=day,
                fertilizer_id=fertilizer_id,
                amount_kg_ha=amount,
                concentration_mg_kg=concentration,
                source=where,
            )
        )

    frozen = {
        day: tuple(sorted(events, key=lambda e: (e.fertilizer_id, e.source)))
        for day, events in by_day.items()
    }
    return FertilizerSchedule(by_day=frozen)