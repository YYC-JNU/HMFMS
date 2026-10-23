"""CSV 输出。

列集合**固定**（ADR-0009）：包含全部过程列，未启用过程的列恒为 0。
列名不做本地化，采用 ``过程_元素`` / ``变量_元素_L层`` 的稳定命名。

三张频率表共用**同一份列契约**（``output_columns``），只是行的粒度和
数值口径不同：

* 通量列（列名以 ``_mg_m2_d`` 结尾）：期内**日均**，单位仍是 mg·m⁻²·d⁻¹；
* 状态列、累积列与闭合列：**期末值**。

这样月表/年表可以严格由日表重聚合得到，同时列名不会因频率而改变。
期总质量 = 日均通量 × 期内天数；天数由 `date` 标签（`YYYY-MM` / `YYYY`）唯一确定。
首行写入 schema 版本、配置指纹与频率；占位模式下全部数值列加
``_PLACEHOLDER`` 后缀，使结果无法被误当正式结果（ADR-0008）。
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Sequence

from hmfms.config.models import OutputFrequency
from hmfms.core.drivers import DRIVER_DEPOSITION_DRY, DRIVER_DEPOSITION_WET
from hmfms.core.integrator import DayRecord
from hmfms.core.registry import PROCESS_NAMES
from hmfms.parameters.resolver import Provenance

SCHEMA_VERSION = 4
META_PREFIX = "#"
PLACEHOLDER_SUFFIX = "_PLACEHOLDER"

Frequency = OutputFrequency

PARAMETERS_COLUMNS = (
    "key",
    "value",
    "unit",
    "dimension",
    "tier",
    "tier_label",
    "source",
    "confidence",
    "note",
)


def output_columns(
    elements: tuple[str, ...],
    layer_ids: tuple[int, ...],
    *,
    placeholder: bool = False,
) -> tuple[str, ...]:
    """所有频率表共用的列契约。

    占位模式只改**列名**，不改列数与顺序——下游脚本一眼能看出结果不可用于正式结论。
    """
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
    if placeholder:
        return ("date", *(f"{column}{PLACEHOLDER_SUFFIX}" for column in columns[1:]))
    return tuple(columns)


@dataclass(frozen=True)
class TableRecord:
    """一张频率表里的一行：标签 + 已按频率聚合好的数值。"""

    label: str
    concentration: dict[str, dict[int, float]]
    mass: dict[str, dict[int, float]]
    deposition_dry: dict[str, float]
    deposition_wet: dict[str, float]
    fluxes: dict[str, dict[str, dict[int, float]]]
    in_total: dict[str, float]
    out_total: dict[str, float]
    net: dict[str, float]
    cumulative_in: dict[str, float]
    cumulative_out: dict[str, float]
    closure: dict[str, float]


def period_label(day: date, frequency: Frequency) -> str:
    if frequency == "daily":
        return day.isoformat()
    if frequency == "monthly":
        return f"{day.year:04d}-{day.month:02d}"
    if frequency == "annual":
        return f"{day.year:04d}"
    raise ValueError(f"未知的输出频率 {frequency!r}")


def _mean(values: list[float]) -> float:
    return math.fsum(values) / len(values)


def _table_record(
    label: str,
    group: list[DayRecord],
    elements: tuple[str, ...],
    layer_ids: tuple[int, ...],
) -> TableRecord:
    last = group[-1]

    def mean_driver(key: str, element: str) -> float:
        return _mean(
            [record.drivers.get(key, {}).get(element, 0.0) for record in group]
        )

    in_total = {e: _mean([r.in_total[e] for r in group]) for e in elements}
    out_total = {e: _mean([r.out_total[e] for r in group]) for e in elements}

    return TableRecord(
        label=label,
        concentration={e: dict(last.concentration[e]) for e in elements},
        mass={e: dict(last.mass[e]) for e in elements},
        deposition_dry={e: mean_driver(DRIVER_DEPOSITION_DRY, e) for e in elements},
        deposition_wet={e: mean_driver(DRIVER_DEPOSITION_WET, e) for e in elements},
        fluxes={
            process: {
                element: {
                    layer_id: _mean(
                        [r.fluxes[process][element][layer_id] for r in group]
                    )
                    for layer_id in layer_ids
                }
                for element in elements
            }
            for process in PROCESS_NAMES
        },
        in_total=in_total,
        out_total=out_total,
        # 净通量沿用"输入 − 输出"，与日表的定义一致。
        net={e: in_total[e] - out_total[e] for e in elements},
        cumulative_in={e: last.cumulative_in[e] for e in elements},
        cumulative_out={e: last.cumulative_out[e] for e in elements},
        closure={e: last.closure[e] for e in elements},
    )


def tabulate(
    records: Sequence[DayRecord],
    frequency: Frequency,
    elements: tuple[str, ...],
    layer_ids: tuple[int, ...],
) -> list[TableRecord]:
    """按频率把日记录聚合成表行。

    分组按记录顺序进行（时间轴已排序），因此输出顺序天然稳定。
    """
    groups: dict[str, list[DayRecord]] = {}
    for record in records:
        label = period_label(record.day, frequency)
        groups.setdefault(label, []).append(record)
    return [
        _table_record(label, group, elements, layer_ids)
        for label, group in groups.items()
    ]


def _meta_line(*, fingerprint: str | None, frequency: Frequency | None) -> str:
    tokens = [f"schema_version={SCHEMA_VERSION}"]
    if fingerprint:
        tokens.append(f"fingerprint={fingerprint}")
    if frequency is not None:
        tokens.append(f"frequency={frequency}")
    return f"{META_PREFIX} " + " ".join(tokens)


def _row(
    record: TableRecord,
    elements: tuple[str, ...],
    layer_ids: tuple[int, ...],
) -> list[str]:
    values: list[str] = [record.label]
    for element in elements:
        for layer_id in layer_ids:
            values.append(f"{record.concentration[element][layer_id]:.12g}")
        for layer_id in layer_ids:
            values.append(f"{record.mass[element][layer_id]:.12g}")
    for element in elements:
        values.append(f"{record.deposition_dry[element]:.12g}")
        values.append(f"{record.deposition_wet[element]:.12g}")
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


def write_parameters_csv(
    path: Path,
    records: Sequence[Provenance],
    *,
    fingerprint: str,
) -> Path:
    """参数溯源表：每个被解析的参数一行，记录它从哪来。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(_meta_line(fingerprint=fingerprint, frequency=None) + "\n")
        writer = csv.writer(handle)
        writer.writerow(PARAMETERS_COLUMNS)
        for record in records:
            writer.writerow(
                [
                    record.key,
                    "" if record.value is None else f"{record.value:.12g}",
                    record.internal_unit,
                    record.dimension,
                    record.tier,
                    record.tier_label,
                    record.source,
                    record.confidence,
                    record.note,
                ]
            )
    return path


def write_table_csv(
    path: Path,
    records: Sequence[TableRecord],
    elements: tuple[str, ...],
    layer_ids: tuple[int, ...],
    *,
    fingerprint: str,
    frequency: Frequency,
    placeholder: bool = False,
) -> Path:
    """写一张频率表；列契约由 :func:`output_columns` 唯一决定。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = output_columns(elements, layer_ids, placeholder=placeholder)

    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(
            _meta_line(fingerprint=fingerprint, frequency=frequency) + "\n"
        )
        handle.write(",".join(columns) + "\n")
        for record in records:
            handle.write(",".join(_row(record, elements, layer_ids)) + "\n")

    return path