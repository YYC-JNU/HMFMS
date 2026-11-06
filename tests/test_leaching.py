"""T15 验收测试：淋溶。

Kd 口径由 `elements/<E>.yaml` 的 `leaching.kd_basis` 显式声明，
数值只来自参数库（ADR-0011）。所有断言都走运行接缝。
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
import yaml

from hmfms.cli import run_from_config
from hmfms.core.errors import ParameterSourceError

FIELD = "field_id: f\narea_ha: 1.0\nlocation:\n  latitude: 26.5\n  longitude: 111.8\n"
SOIL = (
    "layer_id,top,bottom,bulk_density,ph,organic_matter,clay,cec,"
    "initial_Cd,initial_Pb\n"
    "1,0,20,1300,6.5,2.5,12.0,193,0.35,25.0\n"
)

PRECIPITATION = 10.0
ET = 4.0
PERCOLATION = PRECIPITATION - ET
KD = 1200.0
F_EXCH = 0.08
START_CD = 0.35
START_PB = 25.0
SOIL_MASS = 1300.0 * 0.2


def make_case(
    base: Path,
    *,
    days: int = 5,
    kd: float | None = KD,
    f_exch: float | None = None,
    kd_basis: str = "total",
    leaching_enabled: bool = True,
    allow_placeholder: bool = False,
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
                "source": "test value for T15",
            }
        )
    if f_exch is not None:
        library.append(
            {
                "key": "f_exch.Cd",
                "value": f_exch,
                "unit": "-",
                "confidence": "low",
                "source": "test value for T15",
            }
        )
    (base / "params.yaml").write_text(
        yaml.safe_dump({"parameters": library}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    (base / "elements").mkdir(exist_ok=True)
    (base / "elements" / "Cd.yaml").write_text(
        yaml.safe_dump(
            {
                "element": "Cd",
                "leaching": {"enabled": leaching_enabled, "kd_basis": kd_basis},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    config = {
        "name": "leaching-test",
        "version": "0.1.0",
        "time": {"start": "2020-01-01", "end": f"2020-01-{days:02d}", "timestep": "1D"},
        "elements": ["Cd", "Pb"],
        "element_configs": {"Cd": "elements/Cd.yaml"},
        "field": "field.yaml",
        "inputs": {
            "soil": {
                "file": "soil.csv",
                "units": {
                    "depth": "cm",
                    "bulk_density": "kg/m3",
                    "concentration": "mg/kg",
                },
            },
            "weather": {
                "file": "weather.csv",
                "units": {
                    "precipitation": "mm/day",
                    "evapotranspiration": "mm/day",
                },
            },
        },
        "parameters": {"library": "params.yaml"},
        "output": {"directory": "output", "interval": ["daily"]},
    }
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


# ------------------------------------------------------- 公式（式 17/18）


def test_total_basis_flux_matches_hand_calculation(tmp_path: Path) -> None:
    """q = 10 − 4 = 6 mm/d；F = 6 × 0.35 / 1200。"""
    config = make_case(tmp_path)
    rows = read_daily(run_from_config(config).output_path)

    expected = PERCOLATION * START_CD / KD
    assert float(rows[0]["leaching_Cd_mg_m2_d"]) == pytest.approx(expected, rel=1e-12)


def test_labile_basis_multiplies_f_exch(tmp_path: Path) -> None:
    config = make_case(tmp_path, kd_basis="labile", f_exch=F_EXCH)
    rows = read_daily(run_from_config(config).output_path)

    expected = F_EXCH * PERCOLATION * START_CD / KD
    assert float(rows[0]["leaching_Cd_mg_m2_d"]) == pytest.approx(expected, rel=1e-12)


def test_leaching_uses_snapshot_concentration(tmp_path: Path) -> None:
    """第二天用第一天结束时的含量，而不是当天更新后的含量。"""
    config = make_case(tmp_path, days=2)
    rows = read_daily(run_from_config(config).output_path)

    first = PERCOLATION * START_CD / KD
    second = PERCOLATION * (START_CD - first / SOIL_MASS) / KD
    assert float(rows[1]["leaching_Cd_mg_m2_d"]) == pytest.approx(second, rel=1e-12)


def test_mass_balance_matches_accumulated_leaching(tmp_path: Path) -> None:
    config = make_case(tmp_path)
    result = run_from_config(config)
    rows = read_daily(result.output_path)

    leached = sum(float(row["leaching_Cd_mg_m2_d"]) for row in rows)
    start = START_CD * SOIL_MASS
    assert start - result.state.total_mass("Cd") == pytest.approx(leached, rel=1e-12)


def test_closure_error_stays_within_tolerance(tmp_path: Path) -> None:
    config = make_case(tmp_path)
    rows = read_daily(run_from_config(config).output_path)

    for element in ("Cd", "Pb"):
        column = f"closure_error_{element}_mg_m2"
        assert all(abs(float(row[column])) < 1e-9 for row in rows)


def test_non_leaching_element_is_untouched(tmp_path: Path) -> None:
    config = make_case(tmp_path)
    result = run_from_config(config)
    rows = read_daily(result.output_path)

    assert all(float(row["leaching_Pb_mg_m2_d"]) == 0.0 for row in rows)
    assert result.state.total_mass("Pb") == pytest.approx(START_PB * SOIL_MASS, rel=1e-12)


def test_disabled_leaching_is_zero(tmp_path: Path) -> None:
    config = make_case(tmp_path, leaching_enabled=False)
    rows = read_daily(run_from_config(config).output_path)

    assert all(float(row["leaching_Cd_mg_m2_d"]) == 0.0 for row in rows)


# ------------------------------------------------------------- 参数链


def test_missing_kd_errors_with_key_and_doc(tmp_path: Path) -> None:
    config = make_case(tmp_path, kd=None)

    with pytest.raises(ParameterSourceError) as excinfo:
        run_from_config(config)
    message = str(excinfo.value)
    assert "kd.Cd" in message
    assert "docs/parameters.md" in message


def test_labile_basis_requires_f_exch(tmp_path: Path) -> None:
    config = make_case(tmp_path, kd_basis="labile")

    with pytest.raises(ParameterSourceError) as excinfo:
        run_from_config(config)
    assert "f_exch.Cd" in str(excinfo.value)


def test_total_basis_does_not_consume_f_exch(tmp_path: Path) -> None:
    """total 口径下 f_exch 必须是 1，库里即使有该键也不得被读走。"""
    config = make_case(tmp_path, f_exch=F_EXCH)
    result = run_from_config(config)
    keys = {row["key"] for row in read_parameters(result.parameters_path)}

    assert "kd.Cd" in keys
    assert "f_exch.Cd" not in keys

    expected = PERCOLATION * START_CD / KD
    first = read_daily(result.output_path)[0]
    assert float(first["leaching_Cd_mg_m2_d"]) == pytest.approx(expected, rel=1e-12)


def test_zero_kd_rejected(tmp_path: Path) -> None:
    config = make_case(tmp_path, kd=0.0)

    with pytest.raises(ValueError, match="Kd"):
        run_from_config(config)


def test_negative_kd_rejected(tmp_path: Path) -> None:
    config = make_case(tmp_path, kd=-100.0)

    with pytest.raises(ValueError, match="Kd"):
        run_from_config(config)


def test_f_exch_above_one_rejected(tmp_path: Path) -> None:
    config = make_case(tmp_path, kd_basis="labile", f_exch=1.5)

    with pytest.raises(ValueError, match="f_exch"):
        run_from_config(config)


def test_placeholder_kd_cannot_divide_by_zero(tmp_path: Path) -> None:
    config = make_case(tmp_path, kd=None)

    with pytest.raises(ValueError) as excinfo:
        run_from_config(config, allow_placeholder=True)
    message = str(excinfo.value)
    assert "Kd" in message
    assert "占位" in message


# ------------------------------------------------------------- 溯源


def test_kd_is_recorded_with_unit_and_source(tmp_path: Path) -> None:
    config = make_case(tmp_path)
    result = run_from_config(config)
    rows = {row["key"]: row for row in read_parameters(result.parameters_path)}

    record = rows["kd.Cd"]
    assert record["tier"] == "4"
    assert record["unit"] == "L/kg"
    assert float(record["value"]) == pytest.approx(KD)
    assert record["source"] == "test value for T15"


def test_kd_basis_is_recorded_as_choice(tmp_path: Path) -> None:
    config = make_case(tmp_path, kd_basis="labile", f_exch=F_EXCH)
    result = run_from_config(config)
    rows = {row["key"]: row for row in read_parameters(result.parameters_path)}

    record = rows["kd_basis.Cd"]
    assert record["dimension"] == "choice"
    assert "labile" in record["note"]

    f_record = rows["f_exch.Cd"]
    assert f_record["unit"] == "1"
    assert float(f_record["value"]) == pytest.approx(F_EXCH)