"""T06 验收测试：灌溉。

结构与施肥同源，差别在：同日多条必须累加、量纲是 mm × mg/L。
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

import pytest
import yaml

from hmfms.cli import run_from_config
from hmfms.core.errors import ParameterSourceError

FIELD = "field_id: f\narea_ha: 1.0\nlocation:\n  latitude: 26.5\n  longitude: 111.8\n"
SOIL_MASS = 1300.0 * 0.2
START_MASS = 0.35 * SOIL_MASS

LIBRARY = [
    {
        "key": "irr.canal.Cd",
        "value": 0.002,
        "unit": "mg/L",
        "confidence": "low",
        "source": "demo value for tests",
    },
    {
        "key": "irr.canal.Pb",
        "value": 0.01,
        "unit": "mg/L",
        "source": "demo value for tests",
    },
]

COLUMNS = [
    "event_id",
    "date",
    "amount",
    "water_source",
    "Cd_mg_L_override",
    "Pb_mg_L_override",
]


def make_case(
    base: Path,
    *,
    events: list[dict],
    library: list[dict] | None = LIBRARY,
    amount_unit: str = "mm",
    days: int = 5,
) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    (base / "soil.csv").write_text(
        "layer_id,top,bottom,bulk_density,ph,organic_matter,clay,cec,"
        "initial_Cd,initial_Pb\n"
        "1,0,20,1300,6.5,2.5,12.0,193,0.35,25.0\n",
        encoding="utf-8",
    )
    (base / "field.yaml").write_text(FIELD, encoding="utf-8")

    management = base / "management"
    management.mkdir(exist_ok=True)
    with (management / "irrigation_events.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for event in events:
            writer.writerow({column: event.get(column, "") for column in COLUMNS})

    config = {
        "name": "irr-test",
        "version": "0.1.0",
        "time": {
            "start": "2020-01-01",
            "end": f"2020-01-{days:02d}",
            "timestep": "1D",
        },
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
        "management": {
            "irrigation_events": {
                "file": "management/irrigation_events.csv",
                "units": {"amount": amount_unit, "concentration": "mg/L"},
            }
        },
        "output": {"directory": "output", "interval": ["daily"]},
    }
    if library is not None:
        (base / "params.yaml").write_text(
            yaml.safe_dump(
                {"parameters": library}, allow_unicode=True, sort_keys=False
            ),
            encoding="utf-8",
        )
        config["parameters"] = {"library": "params.yaml"}

    path = base / "model.yaml"
    path.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return path


def read_daily(path: Path) -> list[dict[str, str]]:
    return list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()[1:]))


def read_parameters(path: Path) -> list[dict[str, str]]:
    return list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()[1:]))


IRRIGATION = {
    "event_id": "I001",
    "date": "2020-01-02",
    "amount": "50",
    "water_source": "canal",
}


# ------------------------------------------------------------- 守恒


def test_single_irrigation_conserves(tmp_path: Path) -> None:
    config = make_case(tmp_path, events=[IRRIGATION])
    result = run_from_config(config)

    # 50 mm × 0.002 mg/L = 0.1 mg/m2
    assert result.state.total_mass("Cd") - START_MASS == pytest.approx(0.1, rel=1e-12)


def test_same_day_multiple_events_accumulate(tmp_path: Path) -> None:
    second = IRRIGATION | {"event_id": "I002", "amount": "30"}
    config = make_case(tmp_path, events=[IRRIGATION, second])

    total = run_from_config(config).state.total_mass("Cd") - START_MASS
    assert total == pytest.approx((50 + 30) * 0.002, rel=1e-12)


def test_non_event_days_are_zero(tmp_path: Path) -> None:
    config = make_case(tmp_path, events=[IRRIGATION])
    rows = read_daily(run_from_config(config).output_path)

    fluxes = [float(row["irrigation_Cd_mg_m2_d"]) for row in rows]
    assert fluxes[1] == pytest.approx(0.1, rel=1e-12)
    assert all(value == 0.0 for i, value in enumerate(fluxes) if i != 1)


# --------------------------------------------------- 参数链：三级 / 一级


def test_library_concentration_as_tier_three(tmp_path: Path) -> None:
    config = make_case(tmp_path, events=[IRRIGATION])
    result = run_from_config(config)

    record = next(
        row
        for row in read_parameters(result.parameters_path)
        if row["key"] == "irr.canal.Cd"
    )
    assert record["tier"] == "3"
    assert record["unit"] == "mg/L"
    assert float(record["value"]) == pytest.approx(0.002)


def test_override_concentration_as_tier_one(tmp_path: Path) -> None:
    override = IRRIGATION | {"Cd_mg_L_override": "0.01"}
    config = make_case(tmp_path, events=[override])
    result = run_from_config(config)

    total = result.state.total_mass("Cd") - START_MASS
    assert total == pytest.approx(50 * 0.01, rel=1e-12)

    record = next(
        row
        for row in read_parameters(result.parameters_path)
        if row["key"] == "irr.canal.Cd"
    )
    assert record["tier"] == "1"
    assert record["source"] == "irrigation_events.csv:2"


def test_unknown_water_source_reports_location(tmp_path: Path) -> None:
    unknown = IRRIGATION | {"water_source": "reclaimed"}
    config = make_case(tmp_path, events=[unknown])

    with pytest.raises(ParameterSourceError) as excinfo:
        run_from_config(config)
    message = str(excinfo.value)
    assert "irr.reclaimed.Cd" in message
    assert "irrigation_events.csv:2" in message


# --------------------------------------------------------- 坏数据


def test_negative_amount_rejected(tmp_path: Path) -> None:
    config = make_case(tmp_path, events=[IRRIGATION | {"amount": "-10"}])
    with pytest.raises(ValueError, match="为负"):
        run_from_config(config)


def test_outside_window_warns_and_is_ignored(tmp_path: Path, caplog) -> None:
    late = IRRIGATION | {"date": "2021-05-01"}
    config = make_case(tmp_path, events=[late])

    with caplog.at_level(logging.WARNING, logger="hmfms.io.events"):
        result = run_from_config(config)

    assert "落在模拟区间" in caplog.text
    assert result.state.total_mass("Cd") == pytest.approx(START_MASS, rel=1e-12)


# ------------------------------------------------------------- 单位


def test_amount_unit_is_converted(tmp_path: Path) -> None:
    """5 cm = 50 mm。"""
    config = make_case(
        tmp_path, events=[IRRIGATION | {"amount": "5"}], amount_unit="cm"
    )
    total = run_from_config(config).state.total_mass("Cd") - START_MASS
    assert total == pytest.approx(0.1, rel=1e-12)


# --------------------------------------------------- 与降雨无关（INV-1）


def test_irrigation_does_not_depend_on_precipitation(tmp_path: Path) -> None:
    """模型没有任何降雨输入，灌溉通量仍按 mm × 浓度 精确成立。"""
    config = make_case(tmp_path, events=[IRRIGATION])
    result = run_from_config(config)

    assert set(result.records[0].drivers) == set()
    assert result.state.total_mass("Cd") - START_MASS == pytest.approx(
        0.1, rel=1e-12
    )