"""T10 验收测试：输出契约定时化。

覆盖三件事：

1. 日/月/年三种频率可任意组合，三张表共用同一份列契约；
2. 月表与年表能由日表按**声明的聚合规则**重算出来（通量取日均、状态取期末）；
3. 首行有 schema 版本与配置指纹，占位模式把列名与溯源都标出来。
"""

from __future__ import annotations

import csv
import logging
import math
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

from hmfms.cli import run_from_config
from hmfms.core.errors import ParameterSourceError
from hmfms.core.registry import PROCESS_NAMES
from hmfms.io.outputs import SCHEMA_VERSION, output_columns

PLACEHOLDER_SUFFIX = "_PLACEHOLDER"

# 独立的基线列清单（不调用 output_columns 生成）：
# 一旦有人改名、删列或调序，下面的逐列比对必须失败。
BASELINE_CD_L1 = (
    "date",
    "soil_concentration_Cd_L1_mg_kg",
    "soil_mass_Cd_L1_mg_m2",
    "deposition_dry_Cd_mg_m2_d",
    "deposition_wet_Cd_mg_m2_d",
    "deposition_Cd_mg_m2_d",
    "fertilization_Cd_mg_m2_d",
    "irrigation_Cd_mg_m2_d",
    "straw_return_Cd_mg_m2_d",
    "harvest_Cd_mg_m2_d",
    "straw_removal_Cd_mg_m2_d",
    "lumped_loss_Cd_mg_m2_d",
    "leaching_Cd_mg_m2_d",
    "runoff_Cd_mg_m2_d",
    "erosion_Cd_mg_m2_d",
    "volatilization_Cd_mg_m2_d",
    "dust_emission_Cd_mg_m2_d",
    "in_total_Cd_mg_m2_d",
    "out_total_Cd_mg_m2_d",
    "net_flux_Cd_mg_m2_d",
    "cumulative_input_Cd_mg_m2",
    "cumulative_output_Cd_mg_m2",
    "closure_error_Cd_mg_m2",
)

# 期初值取 0.35、容重 1300、层厚 20 cm；沉降按天递增，便于检查聚合。
SOIL_MASS = 1300.0 * 0.2


def _read(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("#"), "首行必须是元信息注释"
    reader = csv.DictReader(lines[1:])
    return list(reader.fieldnames or []), list(reader)


def _meta(path: Path) -> dict[str, str]:
    line = path.read_text(encoding="utf-8").splitlines()[0].lstrip("#").strip()
    tokens = {}
    for token in line.split():
        key, _, value = token.partition("=")
        tokens[key] = value
    return tokens


def make_case(
    base: Path,
    *,
    start: date = date(2020, 1, 30),
    end: date = date(2020, 3, 2),
    interval: tuple[str, ...] = ("daily",),
    library: bool = True,
    blank_ph: bool = False,
) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    (base / "field.yaml").write_text(
        "field_id: f\narea_ha: 1.0\nlocation:\n  latitude: 26.5\n  longitude: 111.8\n",
        encoding="utf-8",
    )
    ph = "" if blank_ph else "6.5"
    (base / "soil.csv").write_text(
        "layer_id,top,bottom,bulk_density,ph,organic_matter,clay,cec,initial_Cd\n"
        f"1,0,20,1300,{ph},2.5,12.0,193,0.35\n",
        encoding="utf-8",
    )

    lines = ["date,dry_Cd,wet_Cd"]
    day = start
    index = 0
    while day <= end:
        index += 1
        lines.append(f"{day.isoformat()},{0.001 * index},0")
        day += timedelta(days=1)
    (base / "deposition.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")

    if library:
        (base / "params.yaml").write_text(
            yaml.safe_dump(
                {
                    "parameters": [
                        {
                            "key": "soil.ph",
                            "value": 6.62,
                            "unit": "-",
                            "source": "test fallback",
                        }
                    ]
                },
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )

    config = {
        "name": "output-contract-test",
        "version": "0.1.0",
        "time": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "timestep": "1D",
        },
        "elements": ["Cd"],
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
            "deposition": {
                "file": "deposition.csv",
                "units": "mg/m2/day",
                "date_column": "date",
                "dry_columns": {"Cd": "dry_Cd"},
                "wet_columns": {"Cd": "wet_Cd"},
                "gap_policy": "error",
            },
        },
        "output": {"directory": "output", "interval": list(interval)},
    }
    if library:
        config["parameters"] = {"library": "params.yaml"}
    path = base / "model.yaml"
    path.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return path


# ------------------------------------------------- 频率组合与列契约


def test_all_frequencies_can_be_combined(tmp_path: Path) -> None:
    config = make_case(tmp_path, interval=("daily", "monthly", "annual"))
    result = run_from_config(config)

    assert set(result.output_paths) == {"daily", "monthly", "annual"}
    assert result.output_paths["daily"].name == "daily.csv"
    assert result.output_paths["monthly"].name == "monthly.csv"
    assert result.output_paths["annual"].name == "annual.csv"

    _, daily = _read(result.output_paths["daily"])
    _, monthly = _read(result.output_paths["monthly"])
    _, annual = _read(result.output_paths["annual"])

    assert len(daily) == 33  # 1/30、1/31、闰年 2/29、3/1、3/2
    assert [row["date"] for row in monthly] == ["2020-01", "2020-02", "2020-03"]
    assert [row["date"] for row in annual] == ["2020"]


def test_only_selected_frequencies_are_written(tmp_path: Path) -> None:
    config = make_case(tmp_path, interval=("annual",))
    result = run_from_config(config)

    assert set(result.output_paths) == {"annual"}
    assert not (tmp_path / "output" / "daily.csv").exists()
    assert not (tmp_path / "output" / "monthly.csv").exists()


def test_all_tables_share_one_column_contract(tmp_path: Path) -> None:
    config = make_case(tmp_path, interval=("daily", "monthly", "annual"))
    result = run_from_config(config)

    assert output_columns(("Cd",), (1,)) == BASELINE_CD_L1
    for path in result.output_paths.values():
        columns, _ = _read(path)
        assert tuple(columns) == BASELINE_CD_L1


# ------------------------------------------------- 首行元信息与配置指纹


def test_meta_line_records_schema_fingerprint_and_frequency(tmp_path: Path) -> None:
    config = make_case(tmp_path, interval=("daily", "monthly", "annual"))
    result = run_from_config(config)

    for frequency, path in result.output_paths.items():
        meta = _meta(path)
        assert meta["schema_version"] == str(SCHEMA_VERSION)
        assert meta["fingerprint"] == result.config_fingerprint
        assert meta["frequency"] == frequency

    assert _meta(result.parameters_path)["fingerprint"] == result.config_fingerprint


def test_fingerprint_is_stable_and_changes_with_a_parameter(tmp_path: Path) -> None:
    first = run_from_config(make_case(tmp_path / "a"))
    second = run_from_config(make_case(tmp_path / "b"))

    assert first.config_fingerprint == second.config_fingerprint

    changed_config = make_case(tmp_path / "c")
    soil = changed_config.parent / "soil.csv"
    soil.write_text(
        soil.read_text(encoding="utf-8").replace(",0.35", ",0.36"), encoding="utf-8"
    )
    changed = run_from_config(changed_config)

    assert changed.config_fingerprint != first.config_fingerprint


def test_fingerprint_ignores_line_ending_style(tmp_path: Path) -> None:
    """同一份配置在 CRLF 与 LF 下必须得到同一指纹。"""
    first = run_from_config(make_case(tmp_path / "a"))

    second_config = make_case(tmp_path / "b")
    for path in second_config.parent.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".yaml", ".csv"}:
            path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))
    second = run_from_config(second_config)

    assert first.config_fingerprint == second.config_fingerprint


def test_annual_rows_split_by_calendar_year(tmp_path: Path) -> None:
    config = make_case(
        tmp_path / "case",
        start=date(2020, 12, 30),
        end=date(2021, 1, 2),
        interval=("annual",),
    )
    _, rows = _read(run_from_config(config).output_paths["annual"])

    assert [row["date"] for row in rows] == ["2020", "2021"]


# --------------------------------------------------------- 重聚合


def _group(rows: list[dict[str, str]], label: str) -> dict[str, list[dict[str, str]]]:
    groups: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        key = row["date"][:label]
        groups.setdefault(key, []).append(row)
    return groups


def _expected_value(column: str, values: list[float]) -> float:
    """通量列取日均；状态、累积与闭合列取期末值（T10 契约）。"""
    if column.endswith("_mg_m2_d"):
        return math.fsum(values) / len(values)
    return values[-1]


@pytest.mark.parametrize("frequency,label_length", [("monthly", 7), ("annual", 4)])
def test_aggregated_tables_reaggregate_from_daily(
    tmp_path: Path, frequency: str, label_length: int
) -> None:
    config = make_case(tmp_path, interval=("daily", frequency))
    result = run_from_config(config)

    daily_columns, daily_rows = _read(result.output_paths["daily"])
    _, aggregated_rows = _read(result.output_paths[frequency])

    grouped = _group(daily_rows, label_length)
    assert [row["date"] for row in aggregated_rows] == list(grouped)

    for aggregated in aggregated_rows:
        rows = grouped[aggregated["date"]]
        for column in daily_columns:
            if column == "date":
                continue
            expected = _expected_value(column, [float(row[column]) for row in rows])
            actual = float(aggregated[column])
            assert abs(actual - expected) < 1e-10, f"{frequency} {column} {aggregated['date']}"


# --------------------------------------------------- 未实现过程列


def test_unimplemented_process_columns_exist_and_are_zero(tmp_path: Path) -> None:
    config = make_case(tmp_path, interval=("daily", "monthly", "annual"))
    result = run_from_config(config)

    implemented = {"deposition"}
    for path in result.output_paths.values():
        columns, rows = _read(path)
        for process in set(PROCESS_NAMES) - implemented:
            column = f"{process}_Cd_mg_m2_d"
            assert column in columns
            assert all(float(row[column]) == 0.0 for row in rows)


# ------------------------------------------------------- 占位模式


def test_placeholder_mode_suffixes_columns_and_logs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    config = make_case(
        tmp_path / "case",
        interval=("daily", "monthly", "annual"),
        library=False,
        blank_ph=True,
    )

    with caplog.at_level(logging.WARNING):
        result = run_from_config(config, allow_placeholder=True)

    for path in result.output_paths.values():
        columns, _ = _read(path)
        assert columns[0] == "date"
        assert len(columns) == len(BASELINE_CD_L1)
        assert all(column.endswith(PLACEHOLDER_SUFFIX) for column in columns[1:])
        stripped = (
            columns[0],
            *(column.removesuffix(PLACEHOLDER_SUFFIX) for column in columns[1:]),
        )
        assert stripped == BASELINE_CD_L1

    messages = [record.getMessage() for record in caplog.records]
    assert any("soil.ph" in message for message in messages)
    assert any("占位" in message for message in messages)

    parameters = {row["key"]: row for row in _read(result.parameters_path)[1]}
    assert parameters["soil.ph"]["tier"] == "0"
    assert parameters["soil.ph"]["tier_label"] == "PLACEHOLDER"


def test_missing_parameter_still_fails_without_the_flag(tmp_path: Path) -> None:
    config = make_case(tmp_path / "case", library=False, blank_ph=True)
    with pytest.raises(ParameterSourceError):
        run_from_config(config)


def test_cli_exposes_the_placeholder_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from hmfms.cli import main

    config = make_case(
        tmp_path / "case", library=False, blank_ph=True, interval=("daily",)
    )
    assert main(["run", str(config), "--allow-placeholder"]) == 0

    output = capsys.readouterr().out
    assert "输出[daily]" in output
    assert "配置指纹" in output