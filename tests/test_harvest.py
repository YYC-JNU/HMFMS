"""T07 验收测试：作物收获与非负保护。

通过运行接缝验证。非负保护是本票第一次让"扣穿"成为可能。
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
import yaml

from hmfms.cli import run_from_config
from hmfms.core.errors import DuplicateEventError, FluxImbalanceError

FIELD = "field_id: f\narea_ha: 1.0\nlocation:\n  latitude: 26.5\n  longitude: 111.8\n"
SOIL_MASS = 1300.0 * 0.2

LIBRARY = [
    {
        "key": "crop.rice.grain.Cd",
        "value": 0.25,
        "unit": "mg/kg",
        "confidence": "medium",
        "source": "J. Hazard. Mater. 2025, 499:140030, Table S4",
    },
    {
        "key": "crop.rice.grain.Pb",
        "value": 0.05,
        "unit": "mg/kg",
        "source": "demo value for tests",
    },
    {
        "key": "crop.rice.straw.Cd",
        "value": 0.78,
        "unit": "mg/kg",
        "source": "J. Hazard. Mater. 2025, 499:140030, Table S4",
    },
    {
        "key": "crop.rice.straw.Pb",
        "value": 0.6,
        "unit": "mg/kg",
        "source": "demo value for tests",
    },
]

COLUMNS = [
    "event_id",
    "date",
    "crop_id",
    "organ",
    "yield",
    "removal_fraction",
    "Cd_mg_kg_override",
    "Pb_mg_kg_override",
]


def make_case(
    base: Path,
    *,
    events: list[dict],
    cd_initial: float = 0.35,
    pb_initial: float = 25.0,
    yield_unit: str = "kg/ha",
    days: int = 5,
) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    (base / "soil.csv").write_text(
        "layer_id,top,bottom,bulk_density,ph,organic_matter,clay,cec,"
        "initial_Cd,initial_Pb\n"
        f"1,0,20,1300,6.5,2.5,12.0,193,{cd_initial},{pb_initial}\n",
        encoding="utf-8",
    )
    (base / "field.yaml").write_text(FIELD, encoding="utf-8")

    management = base / "management"
    management.mkdir(exist_ok=True)
    with (management / "harvest_events.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for event in events:
            writer.writerow({column: event.get(column, "") for column in COLUMNS})

    (base / "params.yaml").write_text(
        yaml.safe_dump({"parameters": LIBRARY}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    config = {
        "name": "harvest-test",
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
            "harvest_events": {
                "file": "management/harvest_events.csv",
                "units": {"yield_rate": yield_unit, "concentration": "mg/kg"},
            }
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
    return list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()[1:]))


def read_parameters(path: Path) -> list[dict[str, str]]:
    return list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()[1:]))


GRAIN = {
    "event_id": "H001",
    "date": "2020-01-03",
    "crop_id": "rice",
    "organ": "grain",
    "yield": "6500",
    "removal_fraction": "1.0",
}
STRAW = GRAIN | {"organ": "straw", "yield": "6700", "removal_fraction": "0.3"}


# ------------------------------------------------------------- 守恒


def test_only_harvest_conserves(tmp_path: Path) -> None:
    config = make_case(tmp_path, events=[GRAIN, STRAW])
    result = run_from_config(config)

    removed = (6500 * 0.25 * 1.0 + 6700 * 0.78 * 0.3) / 10_000.0
    assert 0.35 * SOIL_MASS - result.state.total_mass("Cd") == pytest.approx(
        removed, rel=1e-12
    )


def test_organs_are_valued_separately(tmp_path: Path) -> None:
    config = make_case(tmp_path, events=[GRAIN, STRAW])
    rows = read_daily(run_from_config(config).output_path)

    harvest = [float(row["harvest_Cd_mg_m2_d"]) for row in rows]
    assert harvest[2] == pytest.approx(
        (6500 * 0.25 + 6700 * 0.78 * 0.3) / 10_000.0, rel=1e-12
    )
    assert all(value == 0.0 for i, value in enumerate(harvest) if i != 2)


def test_removal_fraction_scales_linearly(tmp_path: Path) -> None:
    full = make_case(tmp_path / "a", events=[STRAW | {"removal_fraction": "1.0"}])
    half = make_case(tmp_path / "b", events=[STRAW | {"removal_fraction": "0.5"}])

    a = 0.35 * SOIL_MASS - run_from_config(full).state.total_mass("Cd")
    b = 0.35 * SOIL_MASS - run_from_config(half).state.total_mass("Cd")
    assert a == pytest.approx(2.0 * b, rel=1e-12)


def test_concentration_drops_after_harvest(tmp_path: Path) -> None:
    config = make_case(tmp_path, events=[GRAIN])
    rows = read_daily(run_from_config(config).output_path)

    before = float(rows[1]["soil_concentration_Cd_L1_mg_kg"])
    after = float(rows[2]["soil_concentration_Cd_L1_mg_kg"])
    removed = 6500 * 0.25 / 10_000.0
    assert before - after == pytest.approx(removed / SOIL_MASS, rel=1e-12)


# ------------------------------------------------------- 非负保护


def test_removing_more_than_stock_raises(tmp_path: Path) -> None:
    config = make_case(tmp_path, events=[GRAIN], cd_initial=0.0001)

    with pytest.raises(FluxImbalanceError) as excinfo:
        run_from_config(config)

    message = str(excinfo.value)
    assert "2020-01-03" in message
    assert "Cd" in message
    assert "土层 1" in message
    assert "harvest" in message
    assert "H001" in message
    assert "不得裁剪" in message


def test_stock_is_never_negative(tmp_path: Path) -> None:
    """扣穿的场景必须报错，而不是把储量截到 0 继续跑。"""
    config = make_case(tmp_path, events=[GRAIN], cd_initial=0.0001)
    with pytest.raises(FluxImbalanceError):
        run_from_config(config)
    # 报错即终止，不会产出 daily.csv
    assert not (tmp_path / "output" / "daily.csv").exists()


# ------------------------------------------------------- 重复事件


def test_duplicate_organ_on_same_day_raises(tmp_path: Path) -> None:
    duplicate = GRAIN | {"event_id": "H002"}
    config = make_case(tmp_path, events=[GRAIN, duplicate])

    with pytest.raises(DuplicateEventError) as excinfo:
        run_from_config(config)

    message = str(excinfo.value)
    assert "harvest_events.csv" in message
    assert "2020-01-03" in message
    assert "行号: 2, 3" in message


# --------------------------------------------------- 参数链：三级 / 一级


def test_library_concentration_as_tier_three(tmp_path: Path) -> None:
    config = make_case(tmp_path, events=[GRAIN])
    result = run_from_config(config)

    record = next(
        row
        for row in read_parameters(result.parameters_path)
        if row["key"] == "crop.rice.grain.Cd"
    )
    assert record["tier"] == "3"
    assert float(record["value"]) == pytest.approx(0.25)


def test_override_concentration_as_tier_one(tmp_path: Path) -> None:
    override = GRAIN | {"Cd_mg_kg_override": "1.0"}
    config = make_case(tmp_path, events=[override])
    result = run_from_config(config)

    removed = 0.35 * SOIL_MASS - result.state.total_mass("Cd")
    assert removed == pytest.approx(6500 * 1.0 / 10_000.0, rel=1e-12)

    record = next(
        row
        for row in read_parameters(result.parameters_path)
        if row["key"] == "crop.rice.grain.Cd"
    )
    assert record["tier"] == "1"
    assert record["source"] == "harvest_events.csv:2"


# --------------------------------------------------------- 坏数据


@pytest.mark.parametrize("field_name", ["crop_id", "organ"])
def test_missing_identifier_rejected(tmp_path: Path, field_name: str) -> None:
    config = make_case(tmp_path, events=[GRAIN | {field_name: ""}])
    with pytest.raises(ValueError, match=f"{field_name} 为空"):
        run_from_config(config)


def test_removal_fraction_out_of_range_rejected(tmp_path: Path) -> None:
    config = make_case(tmp_path, events=[GRAIN | {"removal_fraction": "1.5"}])
    with pytest.raises(ValueError, match=r"应在 \[0, 1\]"):
        run_from_config(config)


def test_yield_unit_is_converted(tmp_path: Path) -> None:
    """6.5 t/ha = 6500 kg/ha。"""
    config = make_case(
        tmp_path, events=[GRAIN | {"yield": "6.5"}], yield_unit="t/ha"
    )
    result = run_from_config(config)
    removed = 0.35 * SOIL_MASS - result.state.total_mass("Cd")
    assert removed == pytest.approx(6500 * 0.25 / 10_000.0, rel=1e-12)