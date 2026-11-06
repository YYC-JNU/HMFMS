"""T14 验收测试：气象输入与日水量平衡。

全部通过运行接缝验证（配置进、CSV 出，或读 DayRecord.drivers）。
本票不实现径流：q_leach = max(0, P + I − ET) 是**显式边界**，
不是"忘记扣径流"。
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

import pytest
import yaml

from hmfms.cli import run_from_config
from hmfms.core.drivers import DRIVER_ET, DRIVER_PRECIPITATION
from hmfms.core.water import percolation_mm

FIELD = "field_id: f\narea_ha: 1.0\nlocation:\n  latitude: 26.5\n  longitude: 111.8\n"
SOIL = (
    "layer_id,top,bottom,bulk_density,ph,organic_matter,clay,cec,"
    "initial_Cd,initial_Pb\n"
    "1,0,20,1300,6.5,2.5,12.0,193,0.35,25.0\n"
)

KD = 1200.0
START_CONCENTRATION = 0.35


def make_case(
    base: Path,
    *,
    rows: list[tuple[str, float, float]] | None = None,
    days: int = 3,
    precipitation: float = 10.0,
    et: float = 4.0,
    weather_units: tuple[str, str] = ("mm/day", "mm/day"),
    gap_policy: str = "error",
    magnitude: dict | None = None,
    include_et_column: bool = True,
    include_weather: bool = True,
    leaching: bool = True,
    kd_basis: str = "total",
) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    (base / "soil.csv").write_text(SOIL, encoding="utf-8")
    (base / "field.yaml").write_text(FIELD, encoding="utf-8")

    if rows is None:
        rows = [
            (f"2020-01-{index:02d}", precipitation, et)
            for index in range(1, days + 1)
        ]
    if include_et_column:
        lines = ["date,precipitation_mm,et_mm"]
        lines.extend(f"{day},{value},{et_value}" for day, value, et_value in rows)
    else:
        lines = ["date,precipitation_mm"]
        lines.extend(f"{day},{value}" for day, value, _ in rows)
    (base / "weather.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")

    library = [
        {
            "key": "kd.Cd",
            "value": KD,
            "unit": "L/kg",
            "confidence": "low",
            "source": "test value for T14",
        }
    ]
    (base / "params.yaml").write_text(
        yaml.safe_dump({"parameters": library}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    if leaching:
        (base / "elements").mkdir(exist_ok=True)
        (base / "elements" / "Cd.yaml").write_text(
            yaml.safe_dump(
                {
                    "element": "Cd",
                    "leaching": {"enabled": True, "kd_basis": kd_basis},
                },
                sort_keys=False,
            ),
            encoding="utf-8",
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
    if include_weather:
        weather: dict = {
            "file": "weather.csv",
            "date_column": "date",
            "precipitation_column": "precipitation_mm",
            "gap_policy": gap_policy,
            "units": {
                "precipitation": weather_units[0],
                "evapotranspiration": weather_units[1],
            },
        }
        if include_et_column:
            weather["evapotranspiration_column"] = "et_mm"
        if magnitude is not None:
            weather["magnitude_check"] = magnitude
        inputs["weather"] = weather

    config: dict = {
        "name": "weather-test",
        "version": "0.1.0",
        "time": {"start": rows[0][0], "end": rows[-1][0], "timestep": "1D"},
        "elements": ["Cd", "Pb"],
        "field": "field.yaml",
        "inputs": inputs,
        "parameters": {"library": "params.yaml"},
        "output": {"directory": "output", "interval": ["daily"]},
    }
    if leaching:
        config["element_configs"] = {"Cd": "elements/Cd.yaml"}
    path = base / "model.yaml"
    path.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return path


def read_daily(path: Path) -> list[dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return list(csv.DictReader(lines[1:]))


# ------------------------------------------------------- 水量平衡（纯函数）


def test_percolation_is_excess_of_supply_over_et() -> None:
    assert percolation_mm(
        precipitation_mm=10.0, irrigation_mm=5.0, evapotranspiration_mm=4.0
    ) == pytest.approx(11.0)


def test_percolation_is_clipped_at_zero() -> None:
    assert percolation_mm(
        precipitation_mm=0.0, irrigation_mm=0.0, evapotranspiration_mm=5.0
    ) == 0.0


def test_percolation_rejects_negative_water_input() -> None:
    with pytest.raises(ValueError, match="不得为负"):
        percolation_mm(
            precipitation_mm=-1.0, irrigation_mm=0.0, evapotranspiration_mm=0.0
        )


# ------------------------------------------------------------ 驱动装载


def test_drivers_expose_precipitation_and_et(tmp_path: Path) -> None:
    config = make_case(tmp_path, days=2)
    records = run_from_config(config).records

    assert records[0].drivers[DRIVER_PRECIPITATION] == pytest.approx(10.0)
    assert records[0].drivers[DRIVER_ET] == pytest.approx(4.0)


def test_precipitation_unit_is_converted(tmp_path: Path) -> None:
    """1 cm·d⁻¹ = 10 mm·d⁻¹。"""
    config = make_case(
        tmp_path, days=2, precipitation=1.0, weather_units=("cm/day", "mm/day")
    )
    records = run_from_config(config).records
    assert records[0].drivers[DRIVER_PRECIPITATION] == pytest.approx(10.0)


def test_missing_day_errors_with_date_and_policy(tmp_path: Path) -> None:
    rows = [("2020-01-01", 10.0, 0.0), ("2020-01-03", 10.0, 0.0)]
    config = make_case(tmp_path, rows=rows, gap_policy="error")

    with pytest.raises(ValueError) as excinfo:
        run_from_config(config)
    message = str(excinfo.value)
    assert "2020-01-02" in message
    assert "gap_policy=error" in message


def test_missing_day_interpolates_when_configured(tmp_path: Path) -> None:
    rows = [("2020-01-01", 10.0, 0.0), ("2020-01-03", 20.0, 0.0)]
    config = make_case(tmp_path, rows=rows, gap_policy="interpolate")

    records = run_from_config(config).records
    assert records[1].drivers[DRIVER_PRECIPITATION] == pytest.approx(15.0)


def test_missing_et_column_warns_and_uses_zero(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    config = make_case(tmp_path, days=2, include_et_column=False)

    with caplog.at_level(logging.WARNING, logger="hmfms.io.weather"):
        records = run_from_config(config).records

    assert records[0].drivers[DRIVER_ET] == 0.0
    assert "蒸散" in caplog.text


def test_negative_precipitation_rejected(tmp_path: Path) -> None:
    rows = [("2020-01-01", -1.0, 0.0), ("2020-01-02", 10.0, 0.0)]
    config = make_case(tmp_path, rows=rows)

    with pytest.raises(ValueError, match="不得为负"):
        run_from_config(config)


def test_negative_et_rejected(tmp_path: Path) -> None:
    rows = [("2020-01-01", 10.0, -1.0), ("2020-01-02", 10.0, 0.0)]
    config = make_case(tmp_path, rows=rows)

    with pytest.raises(ValueError, match="不得为负"):
        run_from_config(config)


def test_duplicate_date_rejected(tmp_path: Path) -> None:
    rows = [("2020-01-01", 10.0, 0.0), ("2020-01-01", 10.0, 0.0)]
    config = make_case(tmp_path, rows=rows)

    with pytest.raises(ValueError, match="重复"):
        run_from_config(config)


def test_magnitude_out_of_range_warns(tmp_path: Path, caplog) -> None:
    config = make_case(
        tmp_path,
        days=2,
        precipitation=500.0,
        magnitude={"precipitation": {"min": 0.0, "max": 100.0}},
    )

    with caplog.at_level(logging.WARNING, logger="hmfms.io.weather"):
        run_from_config(config)

    assert "超出配置区间" in caplog.text
    assert "单位" in caplog.text


def test_magnitude_within_range_does_not_warn(tmp_path: Path, caplog) -> None:
    config = make_case(
        tmp_path,
        days=2,
        precipitation=10.0,
        magnitude={"precipitation": {"min": 0.0, "max": 100.0}},
    )

    with caplog.at_level(logging.WARNING, logger="hmfms.io.weather"):
        run_from_config(config)

    assert "超出配置区间" not in caplog.text


# ------------------------------------------------------- 与淋溶的接线


def test_leaching_without_weather_is_rejected(tmp_path: Path) -> None:
    config = make_case(tmp_path, days=2, include_weather=False)

    with pytest.raises(ValueError) as excinfo:
        run_from_config(config)
    message = str(excinfo.value)
    assert "inputs.weather" in message
    assert "Cd" in message


def test_weather_without_leaching_warns_it_is_unused(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    config = make_case(tmp_path, days=2, leaching=False)

    with caplog.at_level(logging.WARNING, logger="hmfms.cli"):
        run_from_config(config)

    assert "未参与任何通量" in caplog.text


def test_dry_day_has_no_leaching(tmp_path: Path) -> None:
    """ET > P + I 时下渗量被截到 0，淋溶不得为负。"""
    config = make_case(tmp_path, days=2, precipitation=0.0, et=5.0)
    rows = read_daily(run_from_config(config).output_path)

    assert all(float(row["leaching_Cd_mg_m2_d"]) == 0.0 for row in rows)


def test_irrigation_counts_toward_percolation(tmp_path: Path) -> None:
    """多出来的灌溉水也要参与下渗：q = P + I − ET。"""
    base = tmp_path / "case"
    config = make_case(base, days=3, precipitation=0.0, et=0.0)

    management = base / "management"
    management.mkdir()
    (management / "irrigation_events.csv").write_text(
        "event_id,date,amount,water_source,Cd_mg_L_override,Pb_mg_L_override\n"
        "irr-1,2020-01-02,20,canal,0,0\n",
        encoding="utf-8",
    )

    payload = yaml.safe_load(config.read_text(encoding="utf-8"))
    payload["management"] = {
        "irrigation_events": {
            "file": "management/irrigation_events.csv",
            "units": {"amount": "mm", "concentration": "mg/L"},
        }
    }
    config.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )

    rows = read_daily(run_from_config(config).output_path)
    expected = 20.0 * START_CONCENTRATION / KD
    assert float(rows[1]["leaching_Cd_mg_m2_d"]) == pytest.approx(expected, rel=1e-12)
    assert float(rows[0]["leaching_Cd_mg_m2_d"]) == 0.0