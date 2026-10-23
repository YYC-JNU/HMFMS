"""T13 守恒收口（二）：秸秆、综合收支、单位、时间累积、多元素独立。"""

from __future__ import annotations

from pathlib import Path

import pytest

from hmfms.cli import run_from_config
from test_conservation import initial_mass, make_case


def test_straw_return_and_removal_sum_to_total(tmp_path: Path) -> None:
    result = run_from_config(make_case(
        tmp_path / "straw", days=2,
        straw=[{"event_id": "S1", "date": "2020-01-01", "crop_id": "rice",
                "organ": "straw", "amount": "1000", "return_fraction": "0.6"}]))
    total = 1000 * 0.5 / 10_000.0
    last = result.records[-1]
    assert last.cumulative_in["Cd"] == pytest.approx(total * 0.6, rel=1e-12)
    assert last.cumulative_out["Cd"] == pytest.approx(total * 0.4, rel=1e-12)
    assert last.cumulative_in["Cd"] + last.cumulative_out["Cd"] == pytest.approx(
        total, rel=1e-12)
    assert result.state.total_mass("Cd") - initial_mass("Cd") == pytest.approx(
        total * (2 * 0.6 - 1.0), rel=1e-12)


def test_combined_inputs_and_outputs(tmp_path: Path) -> None:
    result = run_from_config(make_case(
        tmp_path / "combined", days=2,
        deposition={"Cd": (0.001, 0.0005)},
        fertilizer=[{"event_id": "F1", "date": "2020-01-01",
                     "fertilizer_id": "compound_npk", "amount": "100"}],
        irrigation=[{"event_id": "I1", "date": "2020-01-01",
                     "amount": "10", "water_source": "canal"}],
        harvest=[{"event_id": "H1", "date": "2020-01-01", "crop_id": "rice",
                  "organ": "grain", "yield": "1000", "removal_fraction": "1.0"}],
        straw=[{"event_id": "S1", "date": "2020-01-01", "crop_id": "rice",
                "organ": "straw", "amount": "1000", "return_fraction": "0.6"}]))
    expected = 0.003 + 0.01 + 0.01 + 0.03 - 0.02 - 0.02
    last = result.records[-1]
    assert result.state.total_mass("Cd") - initial_mass("Cd") == pytest.approx(
        expected, rel=1e-12)
    assert last.cumulative_in["Cd"] - last.cumulative_out["Cd"] == pytest.approx(
        expected, rel=1e-12)


def test_unit_conversion_g_per_ha_per_day(tmp_path: Path) -> None:
    result = run_from_config(make_case(
        tmp_path / "units", days=3,
        deposition={"Cd": (0.01, 0.0)}, deposition_units="g/ha/day"))
    assert result.state.total_mass("Cd") - initial_mass("Cd") == pytest.approx(
        0.003, rel=1e-12)


def test_time_accumulation_is_linear(tmp_path: Path) -> None:
    short = run_from_config(make_case(
        tmp_path / "short", days=3, deposition={"Cd": (0.001, 0.0)}))
    longer = run_from_config(make_case(
        tmp_path / "long", days=6, deposition={"Cd": (0.001, 0.0)}))
    assert (longer.state.total_mass("Cd") - initial_mass("Cd")) == pytest.approx(
        2 * (short.state.total_mass("Cd") - initial_mass("Cd")), rel=1e-12)


def test_two_elements_are_independent(tmp_path: Path) -> None:
    combined = run_from_config(make_case(
        tmp_path / "both", elements=("Cd", "Pb"), days=4,
        deposition={"Cd": (0.001, 0.0), "Pb": (0.002, 0.0)}))
    cd_only = run_from_config(make_case(
        tmp_path / "cd", days=4, deposition={"Cd": (0.001, 0.0)}))
    pb_only = run_from_config(make_case(
        tmp_path / "pb", elements=("Pb",), days=4,
        deposition={"Pb": (0.002, 0.0)}))
    assert combined.state.total_mass("Cd") == pytest.approx(
        cd_only.state.total_mass("Cd"), rel=1e-12)
    assert combined.state.total_mass("Pb") == pytest.approx(
        pb_only.state.total_mass("Pb"), rel=1e-12)
    for element in ("Cd", "Pb"):
        assert combined.records[-1].relative_closure[element] < 1e-8