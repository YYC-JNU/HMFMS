"""T05 验收测试：事件索引与施肥。

全部通过运行接缝验证。这一组同时把参数解析链的一级（事件覆盖）与
三级（用户选择的库条目）跑成端到端。
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

import pytest
import yaml

from hmfms.cli import run_from_config
from hmfms.core.errors import ParameterSourceError

SOIL = (
    "layer_id,top,bottom,bulk_density,ph,organic_matter,clay,cec,"
    "initial_Cd,initial_Pb\n"
    "1,0,20,1300,6.5,2.5,12.0,193,0.35,25.0\n"
)
FIELD = "field_id: f\narea_ha: 1.0\nlocation:\n  latitude: 26.5\n  longitude: 111.8\n"
START_MASS = 0.35 * (1300.0 * 0.2)

LIBRARY = [
    {
        "key": "fert.compound_npk.Cd",
        "value": 1.31,
        "unit": "mg/kg",
        "confidence": "medium",
        "source": "J. Hazard. Mater. 2025, 499:140030, Table S2",
    },
    {
        "key": "fert.compound_npk.Pb",
        "value": 2.5,
        "unit": "mg/kg",
        "confidence": "low",
        "source": "Shi 2019, STOTEN 649:1269–1280, Table 1（浙江化肥 Pb 区间中值）",
    },
]

EVENT_COLUMNS = ["event_id", "date", "fertilizer_id", "amount",
                 "Cd_mg_kg_override", "Pb_mg_kg_override"]


def make_case(
    base: Path,
    *,
    events: list[dict],
    library: list[dict] | None = LIBRARY,
    days: int = 5,
    amount_unit: str = "kg/ha",
) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    (base / "soil.csv").write_text(SOIL, encoding="utf-8")
    (base / "field.yaml").write_text(FIELD, encoding="utf-8")

    management = base / "management"
    management.mkdir(exist_ok=True)
    with (management / "fertilizer_events.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=EVENT_COLUMNS)
        writer.writeheader()
        for event in events:
            writer.writerow({column: event.get(column, "") for column in EVENT_COLUMNS})

    config = {
        "name": "fert-test",
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
            "fertilizer_events": {
                "file": "management/fertilizer_events.csv",
                "units": {"amount": amount_unit, "concentration": "mg/kg"},
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


EVENT = {
    "event_id": "F001",
    "date": "2020-01-02",
    "fertilizer_id": "compound_npk",
    "amount": "100",
}


# ------------------------------------------------------------- 守恒


def test_single_fertilizer_input_conserves(tmp_path: Path) -> None:
    config = make_case(tmp_path, events=[EVENT])
    result = run_from_config(config)

    # 100 kg/ha × 1.31 mg/kg / 10000 = 0.0131 mg/m2
    assert result.state.total_mass("Cd") - START_MASS == pytest.approx(
        0.0131, rel=1e-12
    )


def test_non_event_days_are_zero(tmp_path: Path) -> None:
    config = make_case(tmp_path, events=[EVENT])
    rows = read_daily(run_from_config(config).output_path)

    fluxes = [float(row["fertilization_Cd_mg_m2_d"]) for row in rows]
    assert fluxes[1] == pytest.approx(0.0131, rel=1e-12)
    assert all(value == 0.0 for i, value in enumerate(fluxes) if i != 1)


def test_same_day_two_types_accumulate(tmp_path: Path) -> None:
    library = LIBRARY + [
        {
            "key": "fert.pig_manure.Cd",
            "value": 1.63,
            "unit": "mg/kg",
            "source": "J. Hazard. Mater. 2025, 499:140030, Table S2",
        },
        {
            "key": "fert.pig_manure.Pb",
            "value": 19.8,
            "unit": "mg/kg",
            "source": "Shi 2019, STOTEN 649, Table 5（猪粪）",
        },
    ]
    second = EVENT | {"event_id": "F002", "fertilizer_id": "pig_manure", "amount": "800"}
    config = make_case(tmp_path, events=[EVENT, second], library=library)

    total = run_from_config(config).state.total_mass("Cd") - START_MASS
    expected = (100 * 1.31 + 800 * 1.63) / 10_000.0
    assert total == pytest.approx(expected, rel=1e-12)


# --------------------------------------------------- 参数链：三级 / 一级


def test_library_entry_used_as_tier_three(tmp_path: Path) -> None:
    config = make_case(tmp_path, events=[EVENT])
    result = run_from_config(config)

    record = next(
        row
        for row in read_parameters(result.parameters_path)
        if row["key"] == "fert.compound_npk.Cd"
    )
    assert record["tier"] == "3"
    assert record["tier_label"] == "library-selected"
    assert float(record["value"]) == pytest.approx(1.31)
    assert "Table S2" in record["source"]


def test_event_override_wins_as_tier_one(tmp_path: Path) -> None:
    override = EVENT | {"Cd_mg_kg_override": "8.0"}
    config = make_case(tmp_path, events=[override])
    result = run_from_config(config)

    total = result.state.total_mass("Cd") - START_MASS
    assert total == pytest.approx(100 * 8.0 / 10_000.0, rel=1e-12)

    record = next(
        row
        for row in read_parameters(result.parameters_path)
        if row["key"] == "fert.compound_npk.Cd"
    )
    assert record["tier"] == "1"
    assert record["tier_label"] == "event-override"
    assert record["source"] == "fertilizer_events.csv:2"


def test_unknown_type_reports_file_and_line(tmp_path: Path) -> None:
    unknown = EVENT | {"fertilizer_id": "mystery_fertilizer"}
    config = make_case(tmp_path, events=[unknown])

    with pytest.raises(ParameterSourceError) as excinfo:
        run_from_config(config)
    message = str(excinfo.value)
    assert "fert.mystery_fertilizer.Cd" in message
    assert "fertilizer_events.csv:2" in message


# --------------------------------------------------------- 坏数据


def test_negative_amount_rejected(tmp_path: Path) -> None:
    config = make_case(tmp_path, events=[EVENT | {"amount": "-5"}])
    with pytest.raises(ValueError, match="为负"):
        run_from_config(config)


def test_duplicate_same_day_same_type_warns_and_accumulates(
    tmp_path: Path, caplog
) -> None:
    config = make_case(tmp_path, events=[EVENT, EVENT | {"event_id": "F009"}])
    with caplog.at_level(logging.WARNING, logger="hmfms.io.events"):
        result = run_from_config(config)

    assert "累加" in caplog.text
    total = result.state.total_mass("Cd") - START_MASS
    assert total == pytest.approx(2 * 100 * 1.31 / 10_000.0, rel=1e-12)


def test_event_outside_window_warns_and_is_ignored(
    tmp_path: Path, caplog
) -> None:
    late = EVENT | {"date": "2021-06-01"}
    config = make_case(tmp_path, events=[late])
    with caplog.at_level(logging.WARNING, logger="hmfms.io.events"):
        result = run_from_config(config)

    assert "落在模拟区间" in caplog.text
    assert result.state.total_mass("Cd") == pytest.approx(START_MASS, rel=1e-12)


def test_amount_unit_is_converted(tmp_path: Path) -> None:
    """0.1 t/ha = 100 kg/ha。"""
    config = make_case(tmp_path, events=[EVENT | {"amount": "0.1"}], amount_unit="t/ha")
    total = run_from_config(config).state.total_mass("Cd") - START_MASS
    assert total == pytest.approx(0.0131, rel=1e-12)