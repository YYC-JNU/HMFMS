"""T13 守恒收口（一）：零输入、单一沉降/施肥/灌溉、仅收获。"""

from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

from hmfms.cli import run_from_config

SOIL_MASS = 1300.0 * 0.2
START = date(2020, 1, 1)

LIBRARY = [
    {"key": "fert.compound_npk.Cd", "value": 1.0, "unit": "mg/kg", "source": "test"},
    {"key": "fert.compound_npk.Pb", "value": 1.0, "unit": "mg/kg", "source": "test"},
    {"key": "irr.canal.Cd", "value": 0.001, "unit": "mg/L", "source": "test"},
    {"key": "irr.canal.Pb", "value": 0.001, "unit": "mg/L", "source": "test"},
    {"key": "crop.rice.grain.Cd", "value": 0.2, "unit": "mg/kg", "source": "test"},
    {"key": "crop.rice.grain.Pb", "value": 0.2, "unit": "mg/kg", "source": "test"},
    {"key": "crop.rice.straw.Cd", "value": 0.5, "unit": "mg/kg", "source": "test"},
    {"key": "crop.rice.straw.Pb", "value": 0.5, "unit": "mg/kg", "source": "test"},
]


def write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def make_case(
    base: Path,
    *,
    elements: tuple[str, ...] = ("Cd",),
    days: int = 5,
    deposition: dict[str, tuple[float, float]] | None = None,
    deposition_units: str = "mg/m2/day",
    fertilizer: list[dict] | None = None,
    irrigation: list[dict] | None = None,
    harvest: list[dict] | None = None,
    straw: list[dict] | None = None,
) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    initials = {"Cd": 0.35, "Pb": 25.0}
    (base / "soil.csv").write_text(
        "layer_id,top,bottom,bulk_density,ph,organic_matter,clay,cec,"
        + ",".join(f"initial_{element}" for element in elements)
        + "\n1,0,20,1300,6.5,2.5,12.0,193,"
        + ",".join(str(initials[element]) for element in elements)
        + "\n",
        encoding="utf-8",
    )
    (base / "field.yaml").write_text(
        "field_id: f\narea_ha: 1.0\nlocation:\n  latitude: 26.5\n  longitude: 111.8\n",
        encoding="utf-8",
    )
    (base / "params.yaml").write_text(
        yaml.safe_dump({"parameters": LIBRARY}, sort_keys=False), encoding="utf-8"
    )
    config: dict = {
        "name": "conservation-test",
        "version": "0.1.0",
        "time": {"start": START.isoformat(),
                 "end": (START + timedelta(days=days - 1)).isoformat(),
                 "timestep": "1D"},
        "elements": list(elements),
        "field": "field.yaml",
        "inputs": {"soil": {"file": "soil.csv", "units": {
            "depth": "cm", "bulk_density": "kg/m3", "concentration": "mg/kg"}}},
        "parameters": {"library": "params.yaml"},
        "output": {"directory": "output", "interval": ["daily"]},
    }
    if deposition is not None:
        columns = ["date"] + [f"{kind}_{element}" for element in elements
                              for kind in ("dry", "wet")]
        rows = []
        for offset in range(days):
            row = {"date": (START + timedelta(days=offset)).isoformat()}
            for element in elements:
                dry, wet = deposition[element]
                row[f"dry_{element}"] = f"{dry:.12g}"
                row[f"wet_{element}"] = f"{wet:.12g}"
            rows.append(row)
        write_csv(base / "deposition.csv", columns, rows)
        config["inputs"]["deposition"] = {
            "file": "deposition.csv", "format": "csv",
            "units": deposition_units, "date_column": "date",
            "dry_columns": {element: f"dry_{element}" for element in elements},
            "wet_columns": {element: f"wet_{element}" for element in elements},
            "gap_policy": "error",
        }
    management: dict = {}
    if fertilizer is not None:
        write_csv(base / "management/fertilizer_events.csv",
                  ["event_id", "date", "fertilizer_id", "amount"], fertilizer)
        management["fertilizer_events"] = {
            "file": "management/fertilizer_events.csv",
            "units": {"amount": "kg/ha", "concentration": "mg/kg"}}
    if irrigation is not None:
        write_csv(base / "management/irrigation_events.csv",
                  ["event_id", "date", "amount", "water_source"], irrigation)
        management["irrigation_events"] = {
            "file": "management/irrigation_events.csv",
            "units": {"amount": "mm", "concentration": "mg/L"}}
    if harvest is not None:
        write_csv(base / "management/harvest_events.csv",
                  ["event_id", "date", "crop_id", "organ", "yield",
                   "removal_fraction"], harvest)
        management["harvest_events"] = {
            "file": "management/harvest_events.csv",
            "units": {"yield_rate": "kg/ha", "concentration": "mg/kg"}}
    if straw is not None:
        write_csv(base / "management/straw_events.csv",
                  ["event_id", "date", "crop_id", "organ", "amount",
                   "return_fraction"], straw)
        management["straw_events"] = {
            "file": "management/straw_events.csv",
            "units": {"amount": "kg/ha", "concentration": "mg/kg"}}
    if management:
        config["management"] = management
    path = base / "model.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path


def initial_mass(element: str) -> float:
    return {"Cd": 0.35, "Pb": 25.0}[element] * SOIL_MASS


def test_zero_input_keeps_stock_and_zero_fluxes(tmp_path: Path) -> None:
    result = run_from_config(make_case(tmp_path / "zero", days=4))
    last = result.records[-1]
    assert result.state.total_mass("Cd") == pytest.approx(initial_mass("Cd"), rel=1e-12)
    assert last.in_total["Cd"] == 0.0
    assert last.out_total["Cd"] == 0.0
    assert last.closure["Cd"] == 0.0


def test_single_deposition_input(tmp_path: Path) -> None:
    result = run_from_config(make_case(
        tmp_path / "dep", days=4, deposition={"Cd": (0.001, 0.0005)}))
    expected = (0.001 + 0.0005) * 4
    assert result.state.total_mass("Cd") - initial_mass("Cd") == pytest.approx(
        expected, rel=1e-12)
    assert result.records[-1].cumulative_in["Cd"] == pytest.approx(
        expected, rel=1e-12)


def test_single_fertilization_input(tmp_path: Path) -> None:
    result = run_from_config(make_case(
        tmp_path / "fert", days=2,
        fertilizer=[{"event_id": "F1", "date": "2020-01-01",
                     "fertilizer_id": "compound_npk", "amount": "100"}]))
    expected = 100 * 1.0 / 10_000.0
    assert result.state.total_mass("Cd") - initial_mass("Cd") == pytest.approx(
        expected, rel=1e-12)


def test_single_irrigation_input(tmp_path: Path) -> None:
    result = run_from_config(make_case(
        tmp_path / "irr", days=2,
        irrigation=[{"event_id": "I1", "date": "2020-01-01",
                     "amount": "10", "water_source": "canal"}]))
    assert result.state.total_mass("Cd") - initial_mass("Cd") == pytest.approx(
        0.01, rel=1e-12)


def test_harvest_only_output(tmp_path: Path) -> None:
    result = run_from_config(make_case(
        tmp_path / "harvest", days=2,
        harvest=[{"event_id": "H1", "date": "2020-01-01", "crop_id": "rice",
                  "organ": "grain", "yield": "1000", "removal_fraction": "1.0"}]))
    assert result.state.total_mass("Cd") - initial_mass("Cd") == pytest.approx(
        -0.02, rel=1e-12)