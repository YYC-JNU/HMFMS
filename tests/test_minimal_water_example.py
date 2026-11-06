"""A2 验收测试：``examples/minimal-water`` 可运行且可手算复核。

示例在临时副本上运行，避免共享目录产生 ``output/`` 或被并发锁住。
"""

from __future__ import annotations

import csv
import shutil
from pathlib import Path

import pytest

from hmfms.cli import run_from_config

EXAMPLE = (
    Path(__file__).resolve().parent.parent / "examples" / "minimal-water"
)

SOIL_MASS = 1300.0 * 0.2
START_CD = 0.35
PRECIPITATION = 10.0
ET = 4.0
FRACTION = 0.5
KD = 300.0
EROSION_RATE_KG_M2_A = 1.07 * 0.1  # t/ha/a → kg/m²/a
ENRICHMENT_RATIO = 1.0


def copy_example(tmp_path: Path) -> Path:
    target = tmp_path / "case"
    shutil.copytree(
        EXAMPLE, target, ignore=shutil.ignore_patterns("output")
    )
    return target


def read_daily(path: Path) -> tuple[str, list[dict[str, str]]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return lines[0], list(csv.DictReader(lines[1:]))


def test_example_writes_schema_5_with_ten_days(tmp_path: Path) -> None:
    result = run_from_config(copy_example(tmp_path) / "model.yaml")
    meta, rows = read_daily(result.output_path)

    assert "schema_version=5" in meta
    assert len(rows) == 10
    assert rows[0]["date"] == "2020-01-01"
    assert rows[-1]["date"] == "2020-01-10"


def test_water_columns_reconcile_with_supply(tmp_path: Path) -> None:
    result = run_from_config(copy_example(tmp_path) / "model.yaml")
    _, rows = read_daily(result.output_path)

    for row in rows:
        precipitation = float(row["water_precipitation_mm_d"])
        et = float(row["water_evapotranspiration_mm_d"])
        runoff = float(row["water_runoff_mm_d"])
        percolation = float(row["water_percolation_mm_d"])
        excess = max(0.0, precipitation - et)
        assert runoff + percolation == pytest.approx(excess, rel=1e-12)
        assert runoff == pytest.approx(FRACTION * excess, rel=1e-12)

    first = rows[0]
    assert float(first["water_precipitation_mm_d"]) == PRECIPITATION
    assert float(first["water_evapotranspiration_mm_d"]) == ET
    assert float(first["water_runoff_mm_d"]) == pytest.approx(3.0)
    assert float(first["water_percolation_mm_d"]) == pytest.approx(3.0)


def test_day_one_fluxes_match_hand_calculation(tmp_path: Path) -> None:
    result = run_from_config(copy_example(tmp_path) / "model.yaml")
    _, rows = read_daily(result.output_path)

    pore_water = START_CD / KD
    runoff_water = FRACTION * (PRECIPITATION - ET)
    percolation_water = (PRECIPITATION - ET) - runoff_water
    erosion = EROSION_RATE_KG_M2_A / 365.0 * ENRICHMENT_RATIO * START_CD

    first = rows[0]
    assert float(first["runoff_Cd_mg_m2_d"]) == pytest.approx(
        runoff_water * pore_water, rel=1e-12
    )
    assert float(first["leaching_Cd_mg_m2_d"]) == pytest.approx(
        percolation_water * pore_water, rel=1e-12
    )
    assert float(first["erosion_Cd_mg_m2_d"]) == pytest.approx(erosion, rel=1e-12)
    assert float(first["out_total_Cd_mg_m2_d"]) == pytest.approx(
        runoff_water * pore_water + percolation_water * pore_water + erosion,
        rel=1e-12,
    )


def test_erosion_continues_on_dry_days(tmp_path: Path) -> None:
    result = run_from_config(copy_example(tmp_path) / "model.yaml")
    _, rows = read_daily(result.output_path)

    dry_day = rows[8]  # 2020-01-09，降水为 0
    assert float(dry_day["water_runoff_mm_d"]) == 0.0
    assert float(dry_day["water_percolation_mm_d"]) == 0.0
    assert float(dry_day["runoff_Cd_mg_m2_d"]) == 0.0
    assert float(dry_day["leaching_Cd_mg_m2_d"]) == 0.0
    assert float(dry_day["erosion_Cd_mg_m2_d"]) > 0.0


def test_mass_balance_matches_accumulated_output(tmp_path: Path) -> None:
    result = run_from_config(copy_example(tmp_path) / "model.yaml")
    _, rows = read_daily(result.output_path)

    losses = sum(float(row["out_total_Cd_mg_m2_d"]) for row in rows)
    start_mass = START_CD * SOIL_MASS
    assert start_mass - result.state.total_mass("Cd") == pytest.approx(
        losses, rel=1e-12
    )
    assert float(rows[-1]["closure_error_Cd_mg_m2"]) == pytest.approx(0.0, abs=1e-9)