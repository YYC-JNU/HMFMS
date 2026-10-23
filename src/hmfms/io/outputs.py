"""CSV 输出。

列集合**固定**（ADR-0009）：包含全部过程列，未启用过程的列恒为 0。
列名不做本地化，采用 ``过程_元素`` / ``变量_元素_L层`` 的稳定命名。

土层相关列随土壤剖面变化（层数是输入数据的一部分），但过程列永远齐全。
"""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Sequence

from hmfms.core.drivers import DRIVER_DEPOSITION_DRY, DRIVER_DEPOSITION_WET
from hmfms.core.integrator import DayRecord
from hmfms.core.registry import PROCESS_NAMES
from hmfms.parameters.resolver import Provenance

SCHEMA_VERSION = 2
META_PREFIX = "#"

PARAMETERS_COLUMNS = (
    "key",
    "value",
    "unit",
    "dimension",
    "tier",
    "tier_label",
    "source",
    "confidence",
)


def daily_columns(
    elements: tuple[str, ...], layer_ids: tuple[int, ...]
) -> tuple[str, ...]:
    columns: list[str] = ["date"]
    for element in elements:
        for layer_id in layer_ids:
            columns.append(f"soil_concentration_{element}_L{layer_id}_mg_kg")
        for layer_id in layer_ids:
            columns.append(f"soil_mass_{element}_L{layer_id}_mg_m2")
    # 沉降的子通量单独成列，使"总量 = 干 + 湿"可以被直接验证。
    for element in elements:
        columns.append(f"deposition_dry_{element}_mg_m2_d")
        columns.append(f"deposition_wet_{element}_mg_m2_d")
    for element in elements:
        for process in PROCESS_NAMES:
            columns.append(f"{process}_{element}_mg_m2_d")
        columns.extend(
            [
                f"in_total_{element}_mg_m2_d",
                f"out_total_{element}_mg_m2_d",
                f"net_flux_{element}_mg_m2_d",
                f"cumulative_input_{element}_mg_m2",
                f"cumulative_output_{element}_mg_m2",
                f"closure_error_{element}_mg_m2",
            ]
        )
    return tuple(columns)


def _row(
    record: DayRecord,
    elements: tuple[str, ...],
    layer_ids: tuple[int, ...],
) -> list[str]:
    values: list[str] = [record.day.isoformat()]
    for element in elements:
        for layer_id in layer_ids:
            values.append(f"{record.concentration[element][layer_id]:.12g}")
        for layer_id in layer_ids:
            values.append(f"{record.mass[element][layer_id]:.12g}")
    for element in elements:
        dry = record.drivers.get(DRIVER_DEPOSITION_DRY, {})
        wet = record.drivers.get(DRIVER_DEPOSITION_WET, {})
        values.append(f"{dry.get(element, 0.0):.12g}")
        values.append(f"{wet.get(element, 0.0):.12g}")
    for element in elements:
        for process in PROCESS_NAMES:
            total = math.fsum(
                record.fluxes[process][element][layer_id]
                for layer_id in layer_ids
            )
            values.append(f"{total:.12g}")
        values.extend(
            [
                f"{record.in_total[element]:.12g}",
                f"{record.out_total[element]:.12g}",
                f"{record.net[element]:.12g}",
                f"{record.cumulative_in[element]:.12g}",
                f"{record.cumulative_out[element]:.12g}",
                f"{record.closure[element]:.12g}",
            ]
        )
    return values


def write_parameters_csv(path: Path, records: Sequence[Provenance]) -> Path:
    """参数溯源表：每个被解析的参数一行，记录它从哪来。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(f"{META_PREFIX} schema_version={SCHEMA_VERSION}\n")
        writer = csv.writer(handle)
        writer.writerow(PARAMETERS_COLUMNS)
        for record in records:
            writer.writerow(
                [
                    record.key,
                    f"{record.value:.12g}",
                    record.internal_unit,
                    record.dimension,
                    record.tier,
                    record.tier_label,
                    record.source,
                    record.confidence,
                ]
            )
    return path


def write_daily_csv(
    path: Path,
    records: list[DayRecord],
    elements: tuple[str, ...],
    layer_ids: tuple[int, ...],
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = daily_columns(elements, layer_ids)

    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(f"{META_PREFIX} schema_version={SCHEMA_VERSION}\n")
        handle.write(",".join(columns) + "\n")
        for record in records:
            handle.write(",".join(_row(record, elements, layer_ids)) + "\n")

    return path