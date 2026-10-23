"""T13 验收测试：十年演示场景与日志收口（在临时副本上运行）。"""

from __future__ import annotations

import csv
import logging
import shutil
from pathlib import Path

import pytest

from hmfms.demo import DEMO_DIR, run_demo, summarize


def demo_copy(tmp_path: Path) -> Path:
    target = tmp_path / "demo"
    shutil.copytree(
        DEMO_DIR, target, ignore=shutil.ignore_patterns("output", "validation")
    )
    return target


def read_rows(path: Path) -> list[dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return list(csv.DictReader(lines[1:]))


def test_demo_is_a_full_ten_year_scenario(tmp_path: Path) -> None:
    demo = run_demo(demo_copy(tmp_path), validate=False)
    rows = read_rows(demo.model.output_paths["daily"])
    assert len(rows) == 3653
    assert rows[0]["date"] == "2015-01-01"
    assert rows[-1]["date"] == "2024-12-31"
    assert set(demo.model.output_paths) == {"daily", "monthly", "annual"}

    annual = demo.annual_delta["Cd"]
    assert 0.0027 / 3 <= annual <= 0.0027 * 3
    for element, residual in demo.model.records[-1].relative_closure.items():
        assert residual < 1e-8, element


def test_demo_is_byte_identical_when_run_twice(tmp_path: Path) -> None:
    base = demo_copy(tmp_path)
    first = run_demo(base, validate=False)
    output_dir = first.model.output_paths["daily"].parent
    snapshot = {
        name: (output_dir / name).read_bytes()
        for name in ("daily.csv", "monthly.csv", "annual.csv")
    }
    snapshot["resolved_parameters.csv"] = first.model.parameters_path.read_bytes()

    second = run_demo(base, validate=False)
    for name, data in snapshot.items():
        assert (output_dir / name).read_bytes() == data


def test_demo_logs_the_upper_bound_and_per_element_severity(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        run_demo(demo_copy(tmp_path), validate=False)
    messages = [record.getMessage() for record in caplog.records]
    assert any("上限" in message for message in messages)
    assert any("一般警告" in message and "Cd" in message for message in messages)
    assert any("强警告" in message and "Pb" in message for message in messages)


def test_demo_includes_observation_comparison(tmp_path: Path) -> None:
    demo = run_demo(demo_copy(tmp_path), validate=True)
    assert demo.validation is not None
    assert demo.validation.report_path.is_file()
    assert demo.validation.figure_path.read_bytes()[:4] == b"\x89PNG"
    assert demo.validation.metrics.n_pairs == 4
    assert "不确定性" in demo.validation.report_path.read_text(encoding="utf-8")
    assert "演示目录" in summarize(demo)