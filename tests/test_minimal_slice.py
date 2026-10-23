"""T02 验收测试：最小可运行纵切。

全部通过运行接缝验证（配置进、CSV 出），不断言内部实现。
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from pydantic import ValidationError

from hmfms.cli import run_from_config
from hmfms.core.errors import ParameterSourceError
from hmfms.core.registry import FLUX_PROCESSES, PROCESS_NAMES, build_default_registry
from hmfms.io.outputs import SCHEMA_VERSION, daily_columns

from conftest import build_case


def read_daily(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("#"), "首行必须是元信息注释"
    reader = csv.DictReader(lines[1:])
    return list(reader.fieldnames or []), list(reader)


# ------------------------------------------------------------------ 基本跑通


def test_writes_daily_csv_with_expected_rows(minimal_case: Path) -> None:
    result = run_from_config(minimal_case)
    columns, rows = read_daily(result.output_path)

    assert len(rows) == 5  # 2020-01-01 至 2020-01-05
    assert columns[0] == "date"
    assert [row["date"] for row in rows] == [
        "2020-01-01",
        "2020-01-02",
        "2020-01-03",
        "2020-01-04",
        "2020-01-05",
    ]


def test_meta_line_records_schema_version(minimal_case: Path) -> None:
    result = run_from_config(minimal_case)
    first = result.output_path.read_text(encoding="utf-8").splitlines()[0]
    assert f"schema_version={SCHEMA_VERSION}" in first


# --------------------------------------------------- 无通量时状态保持不变


def test_soil_content_unchanged_without_fluxes(minimal_case: Path) -> None:
    result = run_from_config(minimal_case)
    _, rows = read_daily(result.output_path)

    for row in rows:
        assert float(row["soil_concentration_Cd_L1_mg_kg"]) == pytest.approx(0.35)
        assert float(row["soil_concentration_Pb_L1_mg_kg"]) == pytest.approx(25.0)


def test_mass_and_concentration_are_consistent(minimal_case: Path) -> None:
    result = run_from_config(minimal_case)
    _, rows = read_daily(result.output_path)
    soil_mass = result.state.soil_mass_kg_m2[1]

    for row in rows:
        mass = float(row["soil_mass_Cd_L1_mg_m2"])
        concentration = float(row["soil_concentration_Cd_L1_mg_kg"])
        assert mass == pytest.approx(concentration * soil_mass, rel=1e-9)


# ---------------------------------------------------- 过程列齐全且恒为零


def test_all_process_columns_present_and_zero(minimal_case: Path) -> None:
    result = run_from_config(minimal_case)
    columns, rows = read_daily(result.output_path)

    expected = daily_columns(("Cd", "Pb"), (1,))
    assert tuple(columns) == expected

    for element in ("Cd", "Pb"):
        for process in PROCESS_NAMES:
            column = f"{process}_{element}_mg_m2_d"
            assert column in columns
            assert all(float(row[column]) == 0.0 for row in rows)


def test_process_contract_covers_all_declared_processes() -> None:
    """注册表、列名、方向三者必须都从同一份清单派生。"""
    registry = build_default_registry()
    assert tuple(module.name for module in registry) == PROCESS_NAMES
    assert {module.name: module.direction for module in registry} == dict(
        FLUX_PROCESSES
    )


# ------------------------------------------------------- 状态维度不退化


def test_state_keeps_two_dimensional_structure(minimal_case: Path) -> None:
    """单层时也不得把「元素 × 土层」压平成「元素」。"""
    result = run_from_config(minimal_case)

    assert isinstance(result.state.mass_mg_m2, dict)
    for element in result.state.elements:
        assert isinstance(result.state.mass_mg_m2[element], dict)
        assert list(result.state.mass_mg_m2[element]) == [1]


# ----------------------------------------------------- 快照语义（顺序无关）


def test_shuffled_module_order_gives_identical_output(tmp_path: Path) -> None:
    first = build_case(tmp_path / "a", elements=("Cd", "Pb"))
    second = build_case(tmp_path / "b", elements=("Cd", "Pb"))

    forward = run_from_config(first, modules=build_default_registry())
    reversed_modules = tuple(reversed(build_default_registry()))
    backward = run_from_config(second, modules=reversed_modules)

    assert forward.output_path.read_bytes() == backward.output_path.read_bytes()


# ------------------------------------------------------------ 质量闭合


def test_closure_error_is_zero_each_step(minimal_case: Path) -> None:
    result = run_from_config(minimal_case)
    _, rows = read_daily(result.output_path)

    for row in rows:
        for element in ("Cd", "Pb"):
            assert float(row[f"closure_error_{element}_mg_m2"]) == 0.0


# -------------------------------------------------------------- 配置校验


def test_rejects_timestep_other_than_one_day(tmp_path: Path) -> None:
    config = build_case(tmp_path / "bad", timestep="12H")
    with pytest.raises(ValidationError) as excinfo:
        run_from_config(config)
    assert "12H" in str(excinfo.value)


def test_rejects_missing_initial_value(tmp_path: Path) -> None:
    """缺初始含量在 T04 之后走参数解析链，报错携带键名与位置。"""
    config = build_case(tmp_path / "missing", elements=("Cd",))
    soil = config.parent / "soil.csv"
    lines = soil.read_text(encoding="utf-8").splitlines()
    lines[-1] = lines[-1].rsplit(",", 1)[0] + ","
    soil.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(ParameterSourceError) as excinfo:
        run_from_config(config)
    message = str(excinfo.value)
    assert "soil.initial.Cd" in message
    assert "为空" in message


# ---------------------------------------------------------- 随仓库的算例


def test_bundled_example_runs() -> None:
    example = Path(__file__).resolve().parent.parent / "examples" / "minimal"
    result = run_from_config(example / "model.yaml")
    _, rows = read_daily(result.output_path)
    assert len(rows) == 10