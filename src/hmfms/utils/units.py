"""单位换算。

模型内部单位（``docs/equations.md`` §0）：

============  ==============
物理量         内部单位
============  ==============
质量           mg
面积           m²
土壤质量       kg·m⁻²
土壤浓度       mg·kg⁻¹
水溶液浓度     mg·L⁻¹
通量           mg·m⁻²·d⁻¹
降雨 / 灌溉    mm
============  ==============

设计约束（T01 验收标准）：

* 纯函数：不读配置、不写日志、不依赖全局状态。
* 换算常数一律以命名常量表达，不出现裸数字。
* 声明未知单位或未知量纲时抛出 :class:`UnitError`，异常文本列出允许值。

依据：``docs/equations.md`` §6（单位换算汇总）与 ``docs/architecture.md`` §2。
"""

from __future__ import annotations

from typing import Final


class UnitError(ValueError):
    """单位或量纲不在白名单内。"""


# ------------------------------------------------------------------ 基本常数

M2_PER_HECTARE: Final[float] = 10_000.0
MG_PER_G: Final[float] = 1_000.0
UG_PER_MG: Final[float] = 1_000.0
MM_PER_METRE: Final[float] = 1_000.0
CM_PER_METRE: Final[float] = 100.0
DAYS_PER_YEAR: Final[float] = 365.0

# 1 mm 降水落在 1 m² 上恰好是 1 L 水，故 mm 与 L·m⁻² 数值相同。
LITRES_PER_MM_PER_M2: Final[float] = 1.0

# 1 g·ha⁻¹ = 1000 mg / 10000 m² = 0.1 mg·m⁻²
MG_PER_M2_PER_G_PER_HA: Final[float] = MG_PER_G / M2_PER_HECTARE


# -------------------------------------------------------------- 单位白名单

ALLOWED_UNITS: Final[dict[str, dict[str, float]]] = {
    # 通量 → mg·m⁻²·d⁻¹
    "flux": {
        "mg/m2/day": 1.0,
        "g/ha/day": MG_PER_M2_PER_G_PER_HA,
        "g/ha/yr": MG_PER_M2_PER_G_PER_HA / DAYS_PER_YEAR,
        "g/ha/a": MG_PER_M2_PER_G_PER_HA / DAYS_PER_YEAR,
        "kg/ha/yr": MG_PER_M2_PER_G_PER_HA * MG_PER_G / DAYS_PER_YEAR,
    },
    # 土壤 / 物料含量 → mg·kg⁻¹（干重口径由调用方保证）
    "concentration": {
        "mg/kg": 1.0,
        "ug/kg": 1.0 / UG_PER_MG,
        "g/kg": MG_PER_G,
    },
    # 水溶液浓度 → mg·L⁻¹
    "water_concentration": {
        "mg/L": 1.0,
        "ug/L": 1.0 / UG_PER_MG,
        "μg/L": 1.0 / UG_PER_MG,
        "ng/L": 1.0 / (UG_PER_MG * MG_PER_G),
    },
    # 水深 → mm
    "water_depth": {
        "mm": 1.0,
        "cm": MM_PER_METRE / CM_PER_METRE,
        "m": MM_PER_METRE,
    },
    # 施用量 / 产量 → kg·ha⁻¹
    "application_rate": {
        "kg/ha": 1.0,
        "t/ha": MG_PER_G,
    },
    # 容重 → kg·m⁻³
    "bulk_density": {
        "kg/m3": 1.0,
        "g/cm3": MG_PER_G,
    },
    # 土层层厚 → m
    "thickness": {
        "m": 1.0,
        "cm": 1.0 / CM_PER_METRE,
    },
    # 无量纲量（pH、有机质 %、黏粒 %、CEC）→ 原值
    "dimensionless": {
        "-": 1.0,
        "1": 1.0,
    },
}


def to_internal(value: float, unit: str, dimension: str) -> float:
    """把 ``value`` 从 ``unit`` 换算成 ``dimension`` 对应的内部单位。

    >>> to_internal(1.0, "g/ha/yr", "flux") > 0
    True
    >>> round(to_internal(1.0, "g/ha/yr", "flux"), 9)
    0.000273973
    """
    if dimension not in ALLOWED_UNITS:
        raise UnitError(
            f"未知量纲 {dimension!r}；可用量纲：{sorted(ALLOWED_UNITS)}"
        )
    table = ALLOWED_UNITS[dimension]
    try:
        factor = table[unit]
    except KeyError:
        raise UnitError(
            f"量纲 {dimension!r} 不支持单位 {unit!r}；"
            f"允许的单位：{sorted(table)}"
        ) from None
    return float(value) * factor


# ------------------------------------------------------------ 物理换算助手
#
# 以下函数把「用户表格里的三个数」直接变成「模型要的那一个数」，
# 避免调用方各自拼公式而拼错系数。每处都标注 equations.md 的式号。


def mass_per_area_from_rate(
    rate_kg_ha: float, concentration_mg_kg: float
) -> float:
    """施用量 / 产量 × 含量 → mg·m⁻²（式 8 与式 11 的公共形式）。

    ``F = R[kg·ha⁻¹] × C[mg·kg⁻¹] / 10000``

    >>> round(mass_per_area_from_rate(100.0, 10.0), 6)
    0.1
    """
    return rate_kg_ha * concentration_mg_kg / M2_PER_HECTARE


def fertilizer_mass_per_area(
    rate_kg_ha: float, concentration_mg_kg: float
) -> float:
    """施肥面通量 → mg·m⁻²（式 8）。

    >>> round(fertilizer_mass_per_area(100.0, 10.0), 6)
    0.1
    """
    return mass_per_area_from_rate(rate_kg_ha, concentration_mg_kg)


def irrigation_mass_per_area(
    amount_mm: float, concentration_mg_l: float
) -> float:
    """灌溉面通量 → mg·m⁻²（式 9）。

    ``F = I[mm] × C_w[mg·L⁻¹]``，因为 1 mm 落在 1 m² 上就是 1 L。

    >>> round(irrigation_mass_per_area(50.0, 0.002), 6)
    0.1
    """
    return amount_mm * LITRES_PER_MM_PER_M2 * concentration_mg_l


def precipitation_deposition_mass_per_area(
    precipitation_mm: float, concentration_ug_l: float
) -> float:
    """由降水中浓度换算沉降面通量 → mg·m⁻²（式 7）。

    ``F = P[mm] × C[μg·L⁻¹] / 1000``

    这是**量纲换算**，不是湿沉降参数化（``INV-1``）。

    >>> round(precipitation_deposition_mass_per_area(1300.0, 0.52), 6)
    0.676
    """
    return (
        precipitation_mm * LITRES_PER_MM_PER_M2 * concentration_ug_l / UG_PER_MG
    )


def soil_mass_per_area(bulk_density_kg_m3: float, thickness_m: float) -> float:
    """单位面积土壤质量 → kg·m⁻²（式 1 的分母）。

    ``M_soil = ρ_b[kg·m⁻³] × d[m]``

    0–20 cm 与 1300 kg·m⁻³ 时为 260 kg·m⁻²。

    >>> round(soil_mass_per_area(1300.0, 0.2), 6)
    260.0
    """
    if bulk_density_kg_m3 <= 0.0:
        raise ValueError(f"容重必须为正，收到 {bulk_density_kg_m3!r}")
    if thickness_m <= 0.0:
        raise ValueError(f"层厚必须为正，收到 {thickness_m!r}")
    return bulk_density_kg_m3 * thickness_m


def mass_to_concentration(
    mass_mg_m2: float, soil_mass_kg_m2: float
) -> float:
    """单位面积储量 → 土壤含量 → mg·kg⁻¹（式 4）。

    ``C = M / M_soil``

    >>> mass_to_concentration(260.0, 260.0)
    1.0
    """
    if soil_mass_kg_m2 <= 0.0:
        raise ValueError(f"单位面积土壤质量必须为正，收到 {soil_mass_kg_m2!r}")
    return mass_mg_m2 / soil_mass_kg_m2


def concentration_to_mass(
    concentration_mg_kg: float, soil_mass_kg_m2: float
) -> float:
    """土壤含量 → 单位面积储量 → mg·m⁻²（式 1）。

    ``M = C × M_soil``

    >>> concentration_to_mass(1.0, 260.0)
    260.0
    """
    if soil_mass_kg_m2 <= 0.0:
        raise ValueError(f"单位面积土壤质量必须为正，收到 {soil_mass_kg_m2!r}")
    return concentration_mg_kg * soil_mass_kg_m2