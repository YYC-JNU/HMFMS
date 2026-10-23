"""从文件装载配置。

三条纪律：

1. **路径一律相对于其所在配置文件**解析，不受当前工作目录影响。
2. 错误信息必须指出**文件与行号**（ADR-0008）。
3. 单位换算只调用 ``hmfms.utils.units``，本文件不出现换算常数。
"""

from __future__ import annotations

import csv
from pathlib import Path

import yaml

from hmfms.config.field import FieldConfig, SoilLayer, SoilProfile
from hmfms.config.models import ModelConfig, SoilUnitsConfig
from hmfms.parameters.resolver import ParameterRequest, ParameterResolver
from hmfms.utils.units import to_internal

REQUIRED_SOIL_COLUMNS: tuple[str, ...] = (
    "layer_id",
    "top",
    "bottom",
    "bulk_density",
    "ph",
    "organic_matter",
    "clay",
    "cec",
)


def _read_yaml(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"配置文件不存在：{path}")
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} 的顶层必须是一个映射（mapping）")
    return payload


def load_model_config(path: Path) -> ModelConfig:
    return ModelConfig.model_validate(_read_yaml(path))


def load_field_config(path: Path) -> FieldConfig:
    return FieldConfig.model_validate(_read_yaml(path))


def initial_column(element: str) -> str:
    """土壤剖面中该元素的初始含量列名。"""
    return f"initial_{element}"


def _num(where: str, row: dict[str, str], key: str) -> float:
    raw = row[key].strip()
    if raw == "":
        raise ValueError(f"{where} 列 {key!r} 为空")
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"{where} 列 {key!r} 的值 {raw!r} 不是数字") from None


SOIL_DOC = "docs/parameters.md §2（土壤属性与初始含量）"


def _display_source(where: str) -> str:
    """把 ``<绝对路径>:<行号>`` 压成 ``文件名:行号``。

    溯源表要能跨机器比较，绝对路径会让两份结果永远不相等。
    """
    path_part, _, line_no = where.rpartition(":")
    if not path_part or not line_no.isdigit():
        return where
    return f"{Path(path_part).name}:{line_no}"


def _soil_value(
    where: str,
    row: dict[str, str],
    column: str,
    *,
    unit: str,
    dimension: str,
    key: str,
    resolver: ParameterResolver | None,
) -> float:
    """所有土壤参数都经解析链取值。

    有实测值 → 二级（local-measured）；为空 → 回落到参数库（四级）；
    两者都没有 → ``ParameterSourceError``。
    """
    text = row[column].strip()
    local = _num(where, row, column) if text else None

    if resolver is None:
        if local is None:
            raise ValueError(
                f"{where} 列 {column!r} 为空，且未配置参数库。"
                "请填入实测值，或在 namelist 中设置 parameters.library。"
            )
        return to_internal(local, unit, dimension)

    return resolver.resolve(
        ParameterRequest(
            key=key,
            unit=unit,
            dimension=dimension,
            local=local,
            local_source=_display_source(where),
            doc_section=SOIL_DOC,
            context=f"{where} 列 {column!r}" + ("" if local is not None else " 为空"),
        )
    )


def load_soil_profile(
    path: Path,
    elements: tuple[str, ...],
    units: SoilUnitsConfig,
    resolver: ParameterResolver | None = None,
) -> SoilProfile:
    if not path.is_file():
        raise FileNotFoundError(f"土壤剖面不存在：{path}")

    raw_rows: list[tuple[int, dict[str, str]]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"{path} 是空文件")
        expected = set(REQUIRED_SOIL_COLUMNS) | {initial_column(e) for e in elements}
        missing = sorted(expected - set(reader.fieldnames))
        if missing:
            raise ValueError(
                f"{path} 缺少必需列 {missing}；实际列为 {list(reader.fieldnames)}"
            )
        for line_no, row in enumerate(reader, start=2):
            raw_rows.append((line_no, row))

    if not raw_rows:
        raise ValueError(f"{path} 中没有任何土层")

    layers: list[SoilLayer] = []
    initial: dict[str, dict[int, float]] = {element: {} for element in elements}

    for line_no, row in raw_rows:
        where = f"{path}:{line_no}"
        layer_id = int(_num(where, row, "layer_id"))
        if any(existing.layer_id == layer_id for existing in layers):
            raise ValueError(f"{where} 土层编号 {layer_id} 重复")

        top_m = to_internal(_num(where, row, "top"), units.depth, "thickness")
        bottom_m = to_internal(_num(where, row, "bottom"), units.depth, "thickness")
        if bottom_m <= top_m:
            raise ValueError(f"{where} 层底 {bottom_m} m 不高于层顶 {top_m} m")

        layers.append(
            SoilLayer(
                layer_id=layer_id,
                top_m=top_m,
                bottom_m=bottom_m,
                bulk_density_kg_m3=_soil_value(
                    where,
                    row,
                    "bulk_density",
                    unit=units.bulk_density,
                    dimension="bulk_density",
                    key="soil.bulk_density",
                    resolver=resolver,
                ),
                ph=_soil_value(
                    where,
                    row,
                    "ph",
                    unit="-",
                    dimension="dimensionless",
                    key="soil.ph",
                    resolver=resolver,
                ),
                organic_matter=_soil_value(
                    where,
                    row,
                    "organic_matter",
                    unit="-",
                    dimension="dimensionless",
                    key="soil.organic_matter",
                    resolver=resolver,
                ),
                clay_fraction=_soil_value(
                    where,
                    row,
                    "clay",
                    unit="-",
                    dimension="dimensionless",
                    key="soil.clay",
                    resolver=resolver,
                ),
                cec=_soil_value(
                    where,
                    row,
                    "cec",
                    unit="-",
                    dimension="dimensionless",
                    key="soil.cec",
                    resolver=resolver,
                ),
            )
        )

        for element in elements:
            column = initial_column(element)
            initial[element][layer_id] = _soil_value(
                where,
                row,
                column,
                unit=units.concentration,
                dimension="concentration",
                key=f"soil.initial.{element}",
                resolver=resolver,
            )

    layers.sort(key=lambda layer: layer.top_m)
    for upper, lower in zip(layers, layers[1:]):
        if lower.top_m < upper.bottom_m:
            raise ValueError(
                f"{path} 土层重叠：{upper.layer_id}({upper.top_m}–{upper.bottom_m} m) "
                f"与 {lower.layer_id}({lower.top_m}–{lower.bottom_m} m)"
            )

    return SoilProfile(layers=tuple(layers), initial_mg_kg=initial)