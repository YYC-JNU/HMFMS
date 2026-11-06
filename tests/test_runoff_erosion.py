"""T16 验收测试：径流、侵蚀与水量诊断列。

记账边界（ADR-0012）：`runoff` 只记溶解态（式 20），`erosion` 记颗粒态（式 22），
式 21 不实现以避免同一份泥沙被扣两次。
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
import yaml

from hmfms.cli import run_from_config
from hmfms.core.errors import ParameterSourceError
from hmfms.io.outputs import SCHEMA_VERSION

FIELD = "field_id: f\narea_ha: 1.0\nlocation:\n  latitude: 26.5\n  longitude: 111.8\n"
SOIL = (
    "layer_id,top,bottom,bulk_density,ph,organic_matter,clay,cec,"
    "initial_Cd,initial_Pb\n"
    "1,0,20,1300,6.5,2.5,12.0,193,0.35,25.0\n"
)

PRECIPITATION = 10.0
ET = 4.0
EXCESS = PRECIPITATION - ET
FRACTION = 0.5
RUNOFF_MM = FRACTION * EXCESS
PERCOLATION_MM = EXCESS - RUNOFF_MM
KD = 300.0
F_EXCH = 0.08
START_CD = 0.35
START_PB = 25.0
SOIL_MASS = 1300.0 * 0.2
EROSION_RATE = 1.0
ER_CD = 2.0
# 1 t·ha⁻¹·a⁻¹ = 0.1 kg·m⁻²·a⁻¹；按 365 天均匀摊分。
EROSION_DAILY_KG_M2 = EROSION_RATE * 0.1 / 365.0


def make_case(
    base: Path,
    *,
    days: int = 5,
    weather: bool = True,
    hydrology: bool = True,
    fraction: float | None = FRACTION,
    leaching: bool = True,
    runoff: bool = True,
    erosion: bool = False,
    kd: float | None = KD,
    f_exch: float | None = None,
    kd_basis: str = "total",
    runoff_basis: str | None = None,
    erosion_rate: float | None = EROSION_RATE,
    er: float | None = ER_CD,
    erosion_rate_unit: str = "t/ha/a",
) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    (base / "soil.csv").write_text(SOIL, encoding="utf-8")
    (base / "field.yaml").write_text(FIELD, encoding="utf-8")

    lines = ["date,precipitation_mm,et_mm"]
    for index in range(1, days + 1):
        lines.append(f"2020-01-{index:02d},{PRECIPITATION},{ET}")
    (base / "weather.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")

    library: list[dict] = []
    if kd is not None:
        library.append(
            {
                "key": "kd.Cd",
                "value": kd,
                "unit": "L/kg",
                "confidence": "low",
                "source": "test value for T16",
            }
        )
    if f_exch is not None:
        library.append(
            {
                "key": "f_exch.Cd",
                "value": f_exch,
                "unit": "-",
                "confidence": "low",
                "source": "test value for T16",
            }
        )
    if erosion_rate is not None:
        library.append(
            {
                "key": "erosion.rate",
                "value": erosion_rate,
                "unit": erosion_rate_unit,
                "confidence": "low",
                "source": "test value for T16",
            }
        )
    if er is not None:
        library.append(
            {
                "key": "erosion.er.Cd",
                "value": er,
                "unit": "-",
                "confidence": "low",
                "source": "test value for T16",
            }
        )
    (base / "params.yaml").write_text(
        yaml.safe_dump({"parameters": library}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    element: dict = {"element": "Cd"}
    if leaching:
        element["leaching"] = {"enabled": True, "kd_basis": kd_basis}
    if runoff:
        element["runoff"] = {
            "enabled": True,
            "kd_basis": runoff_basis or kd_basis,
        }
    if erosion:
        element["erosion"] = {"enabled": True}
    (base / "elements").mkdir(exist_ok=True)
    (base / "elements" / "Cd.yaml").write_text(
        yaml.safe_dump(element, sort_keys=False), encoding="utf-8"
    )

    inputs: dict = {
        "soil": {
            "file": "soil.csv",
            "units": {
                "depth": "cm",
                "bulk_density": "kg/m3",
                "concentration": "mg/kg",
            },
        }
    }
    if weather:
        inputs["weather"] = {
            "file": "weather.csv",
            "units": {
                "precipitation": "mm/day",
                "evapotranspiration": "mm/day",
            },
        }

    config: dict = {
        "name": "runoff-erosion-test",
        "version": "0.1.0",
        "time": {"start": "2020-01-01", "end": f"2020-01-{days:02d}", "timestep": "1D"},
        "elements": ["Cd", "Pb"],
        "element_configs": {"Cd": "elements/Cd.yaml"},
        "field": "field.yaml",
        "inputs": inputs,
        "parameters": {"library": "params.yaml"},
        "output": {"directory": "output", "interval": ["daily"]},
    }
    if hydrology:
        config["hydrology"] = {"runoff_fraction": fraction}
    path = base / "model.yaml"
    path.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return path


def read_daily(path: Path) -> list[dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return list(csv.DictReader(lines[1:]))


def read_parameters(path: Path) -> list[dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return list(csv.DictReader(lines[1:]))


# ------------------------------------------------------------- 水量分配


def test_schema_version_is_five(tmp_path: Path) -> None:
    assert SCHEMA_VERSION == 5
    config = make_case(tmp_path)
    run_from_config(config)
    first = config.parent / "output" / "daily.csv"
    assert "schema_version=5" in first.read_text(encoding="utf-8").splitlines()[0]


def test_water_columns_daily_values(tmp_path: Path) -> None:
    config = make_case(tmp_path)
    rows = read_daily(run_from_config(config).output_path)

    row = rows[0]
    assert float(row["water_precipitation_mm_d"]) == pytest.approx(PRECIPITATION)
    assert float(row["water_evapotranspiration_mm_d"]) == pytest.approx(ET)
    assert float(row["water_irrigation_mm_d"]) == 0.0
    assert float(row["water_runoff_mm_d"]) == pytest.approx(RUNOFF_MM)
    assert float(row["water_percolation_mm_d"]) == pytest.approx(PERCOLATION_MM)


def test_runoff_and_percolation_sum_to_excess(tmp_path: Path) -> None:
    config = make_case(tmp_path)
    rows = read_daily(run_from_config(config).output_path)

    for row in rows:
        runoff = float(row["water_runoff_mm_d"])
        percolation = float(row["water_percolation_mm_d"])
        assert runoff + percolation == pytest.approx(EXCESS, rel=1e-12)


def test_fraction_must_be_between_zero_and_one(tmp_path: Path) -> None:
    config = make_case(tmp_path, fraction=1.5)
    with pytest.raises(ValueError):
        run_from_config(config)


def test_fraction_is_recorded_in_provenance(tmp_path: Path) -> None:
    config = make_case(tmp_path)
    result = run_from_config(config)
    rows = {row["key"]: row for row in read_parameters(result.parameters_path)}

    record = rows["hydrology.runoff_fraction"]
    assert record["tier"] == "2"
    assert record["source"] == "model.yaml:hydrology.runoff_fraction"
    assert float(record["value"]) == pytest.approx(FRACTION)


# ------------------------------------------------------------- 径流


def test_runoff_flux_matches_hand_calculation(tmp_path: Path) -> None:
    config = make_case(tmp_path)
    rows = read_daily(run_from_config(config).output_path)

    expected = RUNOFF_MM * START_CD / KD
    assert float(rows[0]["runoff_Cd_mg_m2_d"]) == pytest.approx(expected, rel=1e-12)


def test_leaching_uses_percolating_water_only(tmp_path: Path) -> None:
    config = make_case(tmp_path)
    rows = read_daily(run_from_config(config).output_path)

    expected = PERCOLATION_MM * START_CD / KD
    assert float(rows[0]["leaching_Cd_mg_m2_d"]) == pytest.approx(expected, rel=1e-12)


def test_zero_fraction_means_no_runoff(tmp_path: Path) -> None:
    config = make_case(tmp_path, fraction=0.0)
    rows = read_daily(run_from_config(config).output_path)

    assert all(float(row["runoff_Cd_mg_m2_d"]) == 0.0 for row in rows)
    expected = EXCESS * START_CD / KD
    assert float(rows[0]["leaching_Cd_mg_m2_d"]) == pytest.approx(expected, rel=1e-12)


def test_labile_runoff_multiplies_f_exch(tmp_path: Path) -> None:
    config = make_case(
        tmp_path,
        leaching=False,
        runoff=True,
        kd_basis="labile",
        f_exch=F_EXCH,
    )
    rows = read_daily(run_from_config(config).output_path)

    expected = F_EXCH * RUNOFF_MM * START_CD / KD
    assert float(rows[0]["runoff_Cd_mg_m2_d"]) == pytest.approx(expected, rel=1e-12)


def test_runoff_requires_hydrology_fraction(tmp_path: Path) -> None:
    config = make_case(tmp_path, hydrology=False, runoff=True)
    with pytest.raises(ValueError, match="runoff_fraction"):
        run_from_config(config)


def test_fraction_without_runoff_is_rejected(tmp_path: Path) -> None:
    config = make_case(tmp_path, hydrology=True, runoff=False, leaching=True)
    with pytest.raises(ValueError, match="runoff"):
        run_from_config(config)


def test_runoff_requires_weather(tmp_path: Path) -> None:
    config = make_case(tmp_path, weather=False, runoff=True)
    with pytest.raises(ValueError, match="inputs.weather"):
        run_from_config(config)


def test_kd_basis_must_match_between_leaching_and_runoff(tmp_path: Path) -> None:
    config = make_case(
        tmp_path, leaching=True, runoff=True, kd_basis="total", runoff_basis="labile"
    )
    with pytest.raises(ValueError, match="kd_basis"):
        run_from_config(config)


def test_runoff_uses_snapshot_concentration(tmp_path: Path) -> None:
    config = make_case(tmp_path, days=2)
    rows = read_daily(run_from_config(config).output_path)

    first_runoff = RUNOFF_MM * START_CD / KD
    first_leaching = PERCOLATION_MM * START_CD / KD
    concentration = START_CD - (first_runoff + first_leaching) / SOIL_MASS
    expected = RUNOFF_MM * concentration / KD
    assert float(rows[1]["runoff_Cd_mg_m2_d"]) == pytest.approx(expected, rel=1e-12)


# ------------------------------------------------------------- 侵蚀


def test_erosion_flux_matches_hand_calculation(tmp_path: Path) -> None:
    config = make_case(
        tmp_path,
        weather=False,
        hydrology=False,
        leaching=False,
        runoff=False,
        erosion=True,
    )
    rows = read_daily(run_from_config(config).output_path)

    expected = EROSION_DAILY_KG_M2 * ER_CD * START_CD
    assert float(rows[0]["erosion_Cd_mg_m2_d"]) == pytest.approx(expected, rel=1e-12)
    assert all(float(row["leaching_Cd_mg_m2_d"]) == 0.0 for row in rows)
    assert all(float(row["runoff_Cd_mg_m2_d"]) == 0.0 for row in rows)


def test_erosion_rate_unit_is_converted(tmp_path: Path) -> None:
    """1000 kg·ha⁻¹·a⁻¹ = 0.1 kg·m⁻²·a⁻¹，与 1 t·ha⁻¹·a⁻¹ 等价。"""
    config = make_case(
        tmp_path,
        weather=False,
        hydrology=False,
        leaching=False,
        runoff=False,
        erosion=True,
        erosion_rate=1000.0,
        erosion_rate_unit="kg/ha/a",
    )
    rows = read_daily(run_from_config(config).output_path)

    expected = EROSION_DAILY_KG_M2 * ER_CD * START_CD
    assert float(rows[0]["erosion_Cd_mg_m2_d"]) == pytest.approx(expected, rel=1e-12)


def test_missing_erosion_rate_errors(tmp_path: Path) -> None:
    config = make_case(
        tmp_path,
        weather=False,
        hydrology=False,
        leaching=False,
        runoff=False,
        erosion=True,
        erosion_rate=None,
    )
    with pytest.raises(ParameterSourceError) as excinfo:
        run_from_config(config)
    assert "erosion.rate" in str(excinfo.value)


def test_missing_erosion_ratio_errors(tmp_path: Path) -> None:
    config = make_case(
        tmp_path,
        weather=False,
        hydrology=False,
        leaching=False,
        runoff=False,
        erosion=True,
        er=None,
    )
    with pytest.raises(ParameterSourceError) as excinfo:
        run_from_config(config)
    assert "erosion.er.Cd" in str(excinfo.value)


# ------------------------------------------------------- 守恒与未启用元素


def test_non_enabled_element_is_untouched(tmp_path: Path) -> None:
    config = make_case(tmp_path, erosion=True)
    result = run_from_config(config)
    rows = read_daily(result.output_path)

    for column in ("leaching_Pb_mg_m2_d", "runoff_Pb_mg_m2_d", "erosion_Pb_mg_m2_d"):
        assert all(float(row[column]) == 0.0 for row in rows)
    assert result.state.total_mass("Pb") == pytest.approx(
        START_PB * SOIL_MASS, rel=1e-12
    )


def test_mass_balance_covers_all_three_processes(tmp_path: Path) -> None:
    config = make_case(tmp_path, erosion=True)
    result = run_from_config(config)
    rows = read_daily(result.output_path)

    losses = 0.0
    for row in rows:
        losses += (
            float(row["leaching_Cd_mg_m2_d"])
            + float(row["runoff_Cd_mg_m2_d"])
            + float(row["erosion_Cd_mg_m2_d"])
        )
    start = START_CD * SOIL_MASS
    assert start - result.state.total_mass("Cd") == pytest.approx(losses, rel=1e-12)


def test_monthly_water_columns_are_period_means(tmp_path: Path) -> None:
    base = tmp_path / "case"
    config = make_case(base)
    payload = yaml.safe_load(config.read_text(encoding="utf-8"))
    payload["output"]["interval"] = ["daily", "monthly"]
    config.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )

    result = run_from_config(config)
    daily = read_daily(result.output_paths["daily"])
    monthly = read_daily(result.output_paths["monthly"])

    for column in (
        "water_precipitation_mm_d",
        "water_runoff_mm_d",
        "water_percolation_mm_d",
    ):
        expected = sum(float(row[column]) for row in daily) / len(daily)
        assert float(monthly[0][column]) == pytest.approx(expected, rel=1e-12)