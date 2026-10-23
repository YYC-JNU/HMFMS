"""单位换算测试（T01）。

断言的是**外部可观察行为**：给定输入与单位，输出的数值是物理上正确的那个。
不测内部实现，不 mock。

数值锚点来自 ``docs/equations.md`` §6 与 §7。
"""

from __future__ import annotations

import pytest

from hmfms.utils.units import (
    ALLOWED_UNITS,
    UnitError,
    concentration_to_mass,
    fertilizer_mass_per_area,
    irrigation_mass_per_area,
    mass_to_concentration,
    precipitation_deposition_mass_per_area,
    soil_mass_per_area,
    to_internal,
)

TOL = 1e-12


# --------------------------------------------------------------- 式 8 施肥


@pytest.mark.parametrize(
    ("rate", "conc", "expected"),
    [
        (100.0, 10.0, 0.1),  # equations.md §6 的示例
        (0.0, 10.0, 0.0),  # 零施用量
        (1516.0, 1.63, 1516.0 * 1.63 / 10_000.0),  # 猪粪实际量级
    ],
)
def test_fertilizer_mass_per_area(rate: float, conc: float, expected: float) -> None:
    assert fertilizer_mass_per_area(rate, conc) == pytest.approx(expected, abs=TOL)


# --------------------------------------------------------------- 式 9 灌溉


@pytest.mark.parametrize(
    ("amount_mm", "conc_mg_l", "expected"),
    [
        (50.0, 0.002, 0.1),  # equations.md §6 的示例
        (0.0, 0.002, 0.0),  # 零灌溉
        (10.0, 0.13, 1.3),  # 0.13 μg/L 换算后为 1.3e-4 mg/L 的量级
    ],
)
def test_irrigation_mass_per_area(
    amount_mm: float, conc_mg_l: float, expected: float
) -> None:
    assert irrigation_mass_per_area(amount_mm, conc_mg_l) == pytest.approx(
        expected, abs=TOL
    )


# ------------------------------------------------------- 式 7 降水浓度→沉降


def test_precipitation_deposition_reproduces_published_value() -> None:
    """用 J. Hazard. Mater. 2025 Table 2 的 6.73 g·ha⁻¹·a⁻¹ 做闭环验证。

    该文实测祁阳大气沉降 6.73 g·ha⁻¹·a⁻¹；按年降水约 1300 mm 反推，
    降水中 Cd 浓度约 0.518 μg/L。把反推值代回换算函数，应还原出原通量。
    """
    # 6.73 g·ha⁻¹·a⁻¹ → mg·m⁻²·a⁻¹
    annual_flux = to_internal(6.73, "g/ha/yr", "flux") * 365.0
    assert annual_flux == pytest.approx(0.673, rel=1e-12)

    # 反推降水浓度（μg/L），再代回
    implied_concentration = annual_flux / 1300.0 * 1_000.0
    assert implied_concentration == pytest.approx(0.5177, abs=1e-4)

    restored = precipitation_deposition_mass_per_area(1300.0, implied_concentration)
    assert restored == pytest.approx(annual_flux, rel=1e-12)


def test_precipitation_deposition_with_rounded_concentration() -> None:
    """取整到 0.52 μg/L 时误差应小于 1%（量级判断不受影响）。"""
    annual_flux = to_internal(6.73, "g/ha/yr", "flux") * 365.0
    rounded = precipitation_deposition_mass_per_area(1300.0, 0.52)
    assert rounded == pytest.approx(0.676, abs=TOL)
    assert rounded == pytest.approx(annual_flux, rel=1e-2)


def test_precipitation_deposition_scales_with_precipitation() -> None:
    half = precipitation_deposition_mass_per_area(650.0, 0.52)
    full = precipitation_deposition_mass_per_area(1300.0, 0.52)
    assert full == pytest.approx(2.0 * half, abs=TOL)


# ----------------------------------------------------- 式 1 土壤质量与储量


def test_soil_mass_per_area_default_profile() -> None:
    """0–20 cm、1300 kg·m⁻³ → 260 kg·m⁻²（equations.md §6）。"""
    assert soil_mass_per_area(1300.0, 0.2) == pytest.approx(260.0, abs=1e-9)


@pytest.mark.parametrize(
    ("bulk_density", "thickness"),
    [(0.0, 0.2), (-100.0, 0.2), (1300.0, 0.0), (1300.0, -0.2)],
)
def test_soil_mass_per_area_rejects_non_positive(
    bulk_density: float, thickness: float
) -> None:
    with pytest.raises(ValueError, match="必须为正"):
        soil_mass_per_area(bulk_density, thickness)


def test_mass_and_concentration_round_trip() -> None:
    soil = soil_mass_per_area(1300.0, 0.2)
    mass = concentration_to_mass(0.35, soil)
    assert mass == pytest.approx(0.35 * 260.0, abs=1e-9)
    assert mass_to_concentration(mass, soil) == pytest.approx(0.35, abs=TOL)


def test_mass_to_concentration_rejects_zero_soil_mass() -> None:
    with pytest.raises(ValueError, match="必须为正"):
        mass_to_concentration(1.0, 0.0)


# ------------------------------------------------------------- 量级自检


def test_annual_accumulation_magnitude() -> None:
    """0.705 mg·m⁻²·a⁻¹ 的净累积 → 约 0.0027 mg·kg⁻¹·a⁻¹（equations.md §7）。

    这是全模型的量级标尺：若实现跑出的年增量与此差一个数量级，一定是
    单位或系数错了。
    """
    soil = soil_mass_per_area(1300.0, 0.2)
    daily_increment = mass_to_concentration(0.705 / 365.0, soil)
    annual_increment = daily_increment * 365.0
    assert annual_increment == pytest.approx(0.0027, abs=5e-5)


# ------------------------------------------------------------- 单位白名单


@pytest.mark.parametrize(
    ("value", "unit", "dimension", "expected"),
    [
        (1.0, "mg/m2/day", "flux", 1.0),
        (1.0, "g/ha/day", "flux", 0.1),
        (1.0, "mg/kg", "concentration", 1.0),
        (1000.0, "ug/kg", "concentration", 1.0),
        (1.0, "g/kg", "concentration", 1000.0),
        (1.0, "mg/L", "water_concentration", 1.0),
        (1000.0, "ug/L", "water_concentration", 1.0),
        (1000.0, "μg/L", "water_concentration", 1.0),
        (1.0, "m", "water_depth", 1000.0),
        (1.0, "cm", "water_depth", 10.0),
        (1.0, "t/ha", "application_rate", 1000.0),
        (1.0, "g/cm3", "bulk_density", 1000.0),
        (1.0, "cm", "thickness", 0.01),
    ],
)
def test_to_internal_conversions(
    value: float, unit: str, dimension: str, expected: float
) -> None:
    assert to_internal(value, unit, dimension) == pytest.approx(expected, abs=TOL)


def test_to_internal_rejects_unknown_unit_and_lists_allowed() -> None:
    with pytest.raises(UnitError) as excinfo:
        to_internal(1.0, "furlong/fortnight", "flux")
    message = str(excinfo.value)
    assert "furlong/fortnight" in message
    # 必须把允许的单位列出来，否则用户无从修起
    for allowed in ALLOWED_UNITS["flux"]:
        assert allowed in message


def test_to_internal_rejects_unknown_dimension_and_lists_allowed() -> None:
    with pytest.raises(UnitError) as excinfo:
        to_internal(1.0, "mg/kg", "density_of_enlightenment")
    assert "density_of_enlightenment" in str(excinfo.value)
    for allowed in ALLOWED_UNITS:
        assert allowed in str(excinfo.value)


def test_unit_error_is_value_error() -> None:
    """调用方可以只捕 ValueError，不必知道 UnitError 的存在。"""
    assert issubclass(UnitError, ValueError)