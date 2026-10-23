"""T12 验收测试：观测对比子命令。

对比层只读**已经生成**的模拟 CSV 与用户观测表，不重跑模型，也不让主模型
接触观测数据（ADR-0006）。
"""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pytest
import yaml

from hmfms.cli import main, run_from_config
from hmfms.validation import run_validation

from conftest import build_case

SIM_COLUMN = "soil_concentration_Cd_L1_mg_kg"


def write_sim(path: Path, rows: list[tuple[str, float]], *, meta: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# schema_version=4 fingerprint=test123 frequency=daily"] if meta else []
    lines.append(f"date,{SIM_COLUMN}")
    for day, value in rows:
        lines.append(f"{day},{value!r}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_obs(
    path: Path,
    rows: list[tuple[str, float]],
    *,
    date_column: str = "sample_date",
    value_column: str = "cd_value",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{date_column},{value_column}"]
    for day, value in rows:
        lines.append(f"{day},{value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def make_config(
    base: Path,
    *,
    rule: str = "exact",
    tolerance_days: int | None = None,
    obs_units: str = "mg/kg",
    trend_min_points: int = 3,
) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    match: dict[str, object] = {"rule": rule}
    if tolerance_days is not None:
        match["tolerance_days"] = tolerance_days
    config = {
        "element": "Cd",
        "layer_id": 1,
        "simulation": {
            "file": "output/daily.csv",
            "date_column": "date",
        },
        "observations": {
            "file": "obs.csv",
            "date_column": "sample_date",
            "value_column": "cd_value",
            "units": obs_units,
        },
        "match": match,
        "metrics": {"trend_min_points": trend_min_points},
        "output": {"directory": "validation"},
    }
    path = base / "validation.yaml"
    path.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return path


def read_points(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


# ------------------------------------------------------------ 指标与产物


def test_exact_matching_metrics_and_artifacts(tmp_path: Path) -> None:
    write_sim(
        tmp_path / "output" / "daily.csv",
        [("2020-01-01", 0.10), ("2020-01-02", 0.20), ("2020-01-03", 0.30)],
    )
    write_obs(
        tmp_path / "obs.csv",
        [("2020-01-01", 0.11), ("2020-01-02", 0.19), ("2020-01-03", 0.35)],
    )
    result = run_validation(make_config(tmp_path))

    metrics = result.metrics
    assert metrics.n_pairs == 3
    assert metrics.mae == pytest.approx((0.01 + 0.01 + 0.05) / 3, rel=1e-12)
    assert metrics.rmse == pytest.approx(0.03, rel=1e-12)
    expected_relative = (
        0.01 / 0.11 + 0.01 / 0.19 + 0.05 / 0.35
    ) / 3
    assert metrics.mean_relative_error == pytest.approx(expected_relative, rel=1e-12)
    assert metrics.end_error == pytest.approx(-0.05, rel=1e-12)
    assert metrics.end_relative_error == pytest.approx(-0.05 / 0.35, rel=1e-12)

    # 只有 3 个点且都在同一年：趋势不可估，必须给说明而不是报错。
    assert metrics.trend_error is None
    assert metrics.trend_note
    assert "不足" in metrics.trend_note

    assert result.report_path.is_file()
    report = result.report_path.read_text(encoding="utf-8")
    assert "MAE" in report and "RMSE" in report
    assert "年际趋势" in report
    assert "不确定性" in report

    assert result.figure_path.read_bytes()[:4] == b"\x89PNG"
    points = read_points(result.points_path)
    assert [row["method"] for row in points] == ["exact", "exact", "exact"]
    assert [row["sim_date"] for row in points] == [
        "2020-01-01",
        "2020-01-02",
        "2020-01-03",
    ]


def test_nearest_and_linear_matching_are_recorded(tmp_path: Path) -> None:
    nearest_base = tmp_path / "nearest"
    write_sim(
        nearest_base / "output" / "daily.csv",
        [("2020-01-01", 0.10), ("2020-01-02", 0.20), ("2020-01-03", 0.30)],
    )
    write_obs(
        nearest_base / "obs.csv",
        [("2020-01-02", 0.20), ("2020-01-04", 0.30)],
    )
    nearest = run_validation(
        make_config(nearest_base, rule="nearest", tolerance_days=2)
    )
    assert [row["method"] for row in read_points(nearest.points_path)] == [
        "exact",
        "nearest",
    ]

    linear_base = tmp_path / "linear"
    write_sim(
        linear_base / "output" / "daily.csv",
        [("2020-01-01", 0.10), ("2020-01-03", 0.30)],
    )
    write_obs(linear_base / "obs.csv", [("2020-01-02", 0.20)])
    linear = run_validation(
        make_config(linear_base, rule="linear", tolerance_days=2)
    )

    point = read_points(linear.points_path)[0]
    assert point["method"] == "linear"
    assert float(point["sim_value_mg_kg"]) == pytest.approx(0.20, rel=1e-12)
    assert point["sim_date"] == "2020-01-01~2020-01-03"


# --------------------------------------------------------- 趋势与不确定性


def test_interannual_trend_is_computed_when_enough_points(tmp_path: Path) -> None:
    write_sim(
        tmp_path / "output" / "daily.csv",
        [
            ("2019-01-01", 0.10),
            ("2020-01-01", 0.20),
            ("2021-01-01", 0.30),
        ],
    )
    write_obs(
        tmp_path / "obs.csv",
        [
            ("2019-01-01", 0.12),
            ("2020-01-01", 0.21),
            ("2021-01-01", 0.33),
        ],
    )
    result = run_validation(make_config(tmp_path))

    x = np.array(
        [
            (np.datetime64("2019-01-01") - np.datetime64("2019-01-01"))
            / np.timedelta64(1, "D"),
            (np.datetime64("2020-01-01") - np.datetime64("2019-01-01"))
            / np.timedelta64(1, "D"),
            (np.datetime64("2021-01-01") - np.datetime64("2019-01-01"))
            / np.timedelta64(1, "D"),
        ]
    ) / 365.25
    expected_sim = float(np.polyfit(x, [0.10, 0.20, 0.30], 1)[0])
    expected_obs = float(np.polyfit(x, [0.12, 0.21, 0.33], 1)[0])

    assert result.metrics.trend_sim == pytest.approx(expected_sim, rel=1e-9)
    assert result.metrics.trend_obs == pytest.approx(expected_obs, rel=1e-9)
    assert result.metrics.trend_error == pytest.approx(
        expected_sim - expected_obs, rel=1e-9
    )
    assert result.metrics.trend_note is None


def test_insufficient_points_do_not_fail(tmp_path: Path) -> None:
    write_sim(
        tmp_path / "output" / "daily.csv",
        [("2020-01-01", 0.10), ("2021-01-01", 0.20)],
    )
    write_obs(
        tmp_path / "obs.csv",
        [("2020-01-01", 0.11), ("2021-01-01", 0.21)],
    )
    result = run_validation(make_config(tmp_path, trend_min_points=3))

    assert result.metrics.n_pairs == 2
    assert result.metrics.trend_error is None
    assert "不足" in (result.metrics.trend_note or "")
    assert "不确定性" in result.report_path.read_text(encoding="utf-8")


# ------------------------------------------------------------- 匹配失败


def test_observation_outside_tolerance_reports_date_and_limit(
    tmp_path: Path,
) -> None:
    write_sim(
        tmp_path / "output" / "daily.csv",
        [("2020-01-01", 0.10), ("2020-01-02", 0.20), ("2020-01-03", 0.30)],
    )
    write_obs(tmp_path / "obs.csv", [("2020-01-10", 0.30)])

    with pytest.raises(ValueError) as excinfo:
        run_validation(
            make_config(tmp_path, rule="nearest", tolerance_days=2)
        )
    message = str(excinfo.value)
    assert "2020-01-10" in message
    assert "2" in message


def test_exact_rule_rejects_missing_date(tmp_path: Path) -> None:
    write_sim(
        tmp_path / "output" / "daily.csv",
        [("2020-01-01", 0.10), ("2020-01-03", 0.30)],
    )
    write_obs(tmp_path / "obs.csv", [("2020-01-02", 0.20)])

    with pytest.raises(ValueError, match="2020-01-02"):
        run_validation(make_config(tmp_path, rule="exact"))


# ------------------------------------------------------------- 数据装载


def test_observation_units_are_converted(tmp_path: Path) -> None:
    write_sim(tmp_path / "output" / "daily.csv", [("2020-01-01", 0.11)])
    write_obs(tmp_path / "obs.csv", [("2020-01-01", 110)])
    result = run_validation(make_config(tmp_path, obs_units="ug/kg"))

    assert result.metrics.mae == pytest.approx(0.0, abs=1e-15)


def test_missing_observation_column_lists_actual_columns(tmp_path: Path) -> None:
    write_sim(tmp_path / "output" / "daily.csv", [("2020-01-01", 0.11)])
    (tmp_path / "obs.csv").write_text(
        "sample_date,other\n2020-01-01,0.1\n", encoding="utf-8"
    )

    with pytest.raises(ValueError) as excinfo:
        run_validation(make_config(tmp_path))
    message = str(excinfo.value)
    assert "cd_value" in message
    assert "other" in message


def test_duplicate_observation_dates_are_rejected(tmp_path: Path) -> None:
    write_sim(
        tmp_path / "output" / "daily.csv",
        [("2020-01-01", 0.10), ("2020-01-02", 0.20)],
    )
    write_obs(
        tmp_path / "obs.csv",
        [("2020-01-01", 0.11), ("2020-01-01", 0.12)],
    )

    with pytest.raises(ValueError, match="重复"):
        run_validation(make_config(tmp_path))


# ------------------------------------------------------------- CLI 与独立性


def test_validate_subcommand_runs_without_the_model(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_sim(tmp_path / "output" / "daily.csv", [("2020-01-01", 0.11)])
    write_obs(tmp_path / "obs.csv", [("2020-01-01", 0.11)])
    config = make_config(tmp_path)

    assert main(["validate", str(config)]) == 0
    output = capsys.readouterr().out
    assert "报告" in output
    assert (tmp_path / "validation" / "report.md").is_file()


def test_model_runs_without_any_observation_file(tmp_path: Path) -> None:
    case = tmp_path / "case"
    result = run_from_config(build_case(case))

    assert result.output_paths["daily"].is_file()
    assert not list(case.glob("*obs*"))