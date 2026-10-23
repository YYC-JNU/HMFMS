"""T09 验收测试：秸秆还田与外运（`INV-2`）。

一个秸秆事件同时产生一条输入与一条输出，两者必须严格互补。
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
import yaml

from hmfms.cli import run_from_config
from hmfms.core.errors import DuplicateEventError, ParameterSourceError

FIELD = "field_id: f\narea_ha: 1.0\nlocation:\n  latitude: 26.5\n  longitude: 111.8\n"
SOIL_MASS = 1300.0 * 0.2
START_MASS = 0.35 * SOIL_MASS

LIBRARY = [
    {"key": "crop.rice.grain.Cd", "value": 0.25, "unit": "mg/kg", "source": "test"},
    {"key": "crop.rice.grain.Pb", "value": 0.05, "unit": "mg/kg", "source": "test"},
    {"key": "crop.rice.straw.Cd", "value": 0.78, "unit": "mg/kg", "source": "test"},
    {"key": "crop.rice.straw.Pb", "value": 0.6, "unit": "mg/kg", "source": "test"},
]

STRAW_COLUMNS = [
    "event_id",
    "date",
    "crop_id",
    "organ",
    "amount",
    "return_fraction",
    "Cd_mg_kg_override",
    "Pb_mg_kg_override",
]
HARVEST_COLUMNS = [
    "event_id",
    "date",
    "crop_id",
    "organ",
    "yield",
    "removal_fraction",
    "Cd_mg_kg_override",
    "Pb_mg_kg_override",
]


def _write(
    path: Path, columns: list[str], rows: list[dict]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def make_case(
    base: Path,
    *,
    straw: list[dict] | None = None,
    harvest: list[dict] | None = None,
    days: int = 6,
) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    (base / "soil.csv").write_text(
        "layer_id,top,bottom,bulk_density,ph,organic_matter,clay,cec,"
        "initial_Cd,initial_Pb\n"
        "1,0,20,1300,6.5,2.5,12.0,193,0.35,25.0\n",
        encoding="utf-8",
    )
    (base / "field.yaml").write_text(FIELD, encoding="utf-8")
    (base / "params.yaml").write_text(
        yaml.safe_dump({"parameters": LIBRARY}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    management: dict = {}
    if straw is not None:
        _write(base / "management" / "straw_events.csv", STRAW_COLUMNS, straw)
        management["straw_events"] = {
            "file": "management/straw_events.csv",
            "units": {"amount": "kg/ha", "concentration": "mg/kg"},
        }
    if harvest is not None:
        _write(base / "management" / "harvest_events.csv", HARVEST_COLUMNS, harvest)
        management["harvest_events"] = {
            "file": "management/harvest_events.csv",
            "units": {"yield_rate": "kg/ha", "concentration": "mg/kg"},
        }

    config = {
        "name": "straw-test",
        "version": "0.1.0",
        "time": {"start": "2020-01-01", "end": f"2020-01-{days:02d}", "timestep": "1D"},
        "elements": ["Cd", "Pb"],
        "field": "field.yaml",
        "inputs": {
            "soil": {
                "file": "soil.csv",
                "units": {
                    "depth": "cm",
                    "bulk_density": "kg/m3",
                    "concentration": "mg/kg",
                },
            }
        },
        "management": management,
        "parameters": {"library": "params.yaml"},
        "output": {"directory": "output", "interval": ["daily"]},
    }
    path = base / "model.yaml"
    path.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return path


def read_daily(path: Path) -> list[dict[str, str]]:
    return list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()[1:]))


STRAW = {
    "event_id": "S001",
    "date": "2020-01-03",
    "crop_id": "rice",
    "organ": "straw",
    "amount": "6700",
    "return_fraction": "0.7",
}
TOTAL_CD = 6700 * 0.78 / 10_000.0


# ------------------------------------------------------------- INV-2


def test_return_plus_removal_equals_total(tmp_path: Path) -> None:
    config = make_case(tmp_path, straw=[STRAW])
    rows = read_daily(run_from_config(config).output_path)

    row = rows[2]
    returned = float(row["straw_return_Cd_mg_m2_d"])
    removed = float(row["straw_removal_Cd_mg_m2_d"])
    assert returned == pytest.approx(TOTAL_CD * 0.7, rel=1e-12)
    assert removed == pytest.approx(TOTAL_CD * 0.3, rel=1e-12)
    assert returned + removed == pytest.approx(TOTAL_CD, rel=1e-12)


def test_fraction_change_swaps_the_two_columns(tmp_path: Path) -> None:
    high = make_case(tmp_path / "high", straw=[STRAW | {"return_fraction": "0.9"}])
    low = make_case(tmp_path / "low", straw=[STRAW | {"return_fraction": "0.1"}])

    high_row = read_daily(run_from_config(high).output_path)[2]
    low_row = read_daily(run_from_config(low).output_path)[2]

    high_total = float(high_row["straw_return_Cd_mg_m2_d"]) + float(
        high_row["straw_removal_Cd_mg_m2_d"]
    )
    low_total = float(low_row["straw_return_Cd_mg_m2_d"]) + float(
        low_row["straw_removal_Cd_mg_m2_d"]
    )
    assert high_total == pytest.approx(low_total, rel=1e-12)
    assert float(high_row["straw_return_Cd_mg_m2_d"]) == pytest.approx(
        TOTAL_CD * 0.9, rel=1e-12
    )
    assert float(low_row["straw_return_Cd_mg_m2_d"]) == pytest.approx(
        TOTAL_CD * 0.1, rel=1e-12
    )


# ------------------------------------------------------------- 端点


def test_all_removed_endpoint(tmp_path: Path) -> None:
    """f = 0：全外运，土壤净减少一个总带出量。"""
    config = make_case(tmp_path, straw=[STRAW | {"return_fraction": "0"}])
    result = run_from_config(config)

    assert result.state.total_mass("Cd") - START_MASS == pytest.approx(
        -TOTAL_CD, rel=1e-12
    )


def test_all_returned_endpoint(tmp_path: Path) -> None:
    """f = 1：全还田，土壤净增加一个总带出量。"""
    config = make_case(tmp_path, straw=[STRAW | {"return_fraction": "1"}])
    result = run_from_config(config)

    assert result.state.total_mass("Cd") - START_MASS == pytest.approx(
        TOTAL_CD, rel=1e-12
    )


def test_net_effect_follows_two_f_minus_one(tmp_path: Path) -> None:
    """只有秸秆事件时：期末 − 期初 = 总带出量 × (2f − 1)。"""
    config = make_case(tmp_path, straw=[STRAW | {"return_fraction": "0.7"}])
    result = run_from_config(config)

    assert result.state.total_mass("Cd") - START_MASS == pytest.approx(
        TOTAL_CD * (2 * 0.7 - 1), rel=1e-12
    )


# --------------------------------------------------------- 坏数据


def test_duplicate_straw_event_raises(tmp_path: Path) -> None:
    config = make_case(tmp_path, straw=[STRAW, STRAW | {"event_id": "S002"}])

    with pytest.raises(DuplicateEventError) as excinfo:
        run_from_config(config)
    message = str(excinfo.value)
    assert "straw_events.csv" in message
    assert "2020-01-03" in message
    assert "行号: 2, 3" in message


def test_missing_crop_id_rejected(tmp_path: Path) -> None:
    config = make_case(tmp_path, straw=[STRAW | {"crop_id": ""}])
    with pytest.raises(ValueError, match="crop_id 为空"):
        run_from_config(config)


def test_return_fraction_out_of_range_rejected(tmp_path: Path) -> None:
    config = make_case(tmp_path, straw=[STRAW | {"return_fraction": "1.2"}])
    with pytest.raises(ValueError, match=r"应在 \[0, 1\]"):
        run_from_config(config)


def test_unknown_crop_organ_reports_location(tmp_path: Path) -> None:
    unknown = STRAW | {"crop_id": "maize"}
    config = make_case(tmp_path, straw=[unknown])

    with pytest.raises(ParameterSourceError) as excinfo:
        run_from_config(config)
    message = str(excinfo.value)
    assert "crop.maize.straw.Cd" in message
    assert "straw_events.csv:2" in message


# ------------------------------------- 交叉校验：同一器官不得两处处理


def test_same_organ_in_both_tables_rejected(tmp_path: Path) -> None:
    harvest = {
        "event_id": "H001",
        "date": "2020-01-02",
        "crop_id": "rice",
        "organ": "straw",
        "yield": "6700",
        "removal_fraction": "0.3",
    }
    config = make_case(tmp_path, straw=[STRAW], harvest=[harvest])

    with pytest.raises(ValueError, match="同时出现在收获表与秸秆表"):
        run_from_config(config)


def test_different_organs_in_two_tables_is_fine(tmp_path: Path) -> None:
    harvest = {
        "event_id": "H001",
        "date": "2020-01-02",
        "crop_id": "rice",
        "organ": "grain",
        "yield": "6500",
        "removal_fraction": "1.0",
    }
    config = make_case(tmp_path, straw=[STRAW], harvest=[harvest])
    result = run_from_config(config)

    grain_out = 6500 * 0.25 / 10_000.0
    straw_net = TOTAL_CD * (2 * 0.7 - 1)
    assert result.state.total_mass("Cd") - START_MASS == pytest.approx(
        straw_net - grain_out, rel=1e-12
    )