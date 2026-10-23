"""T04 验收测试：参数解析五级链与溯源表。

绝大多数用例走**运行接缝**（配置进、CSV 出）。
只有一组例外：一级（事件覆盖）与三级（用户选择的库条目）在生产代码中
要到 T05 才有调用方，因此它们的顺序性在这里以解析器的纯函数测试兜住，
并在 T05 接上真实调用后由端到端测试接管。
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

import pytest
import yaml

from hmfms.cli import run_from_config
from hmfms.core.errors import ParameterSourceError
from hmfms.parameters import ParameterRequest, ParameterResolver, load_library
from hmfms.parameters.library import ParameterEntry

from conftest import build_case


def read_parameters(path: Path) -> list[dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("#")
    return list(csv.DictReader(lines[1:]))


def attach_library(config_path: Path, entries: list[dict]) -> Path:
    base = config_path.parent
    library = base / "params.yaml"
    library.write_text(
        yaml.safe_dump({"parameters": entries}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    text = config_path.read_text(encoding="utf-8")
    config_path.write_text(
        text + "\nparameters:\n  library: params.yaml\n", encoding="utf-8"
    )
    return library


def blank_cell(config_path: Path, column: str) -> None:
    """把 soil.csv 中某一列的值清空，模拟"没有实测"。"""
    soil = config_path.parent / "soil.csv"
    lines = soil.read_text(encoding="utf-8").splitlines()
    index = lines[0].split(",").index(column)
    fields = lines[1].split(",")
    fields[index] = ""
    lines[1] = ",".join(fields)
    soil.write_text("\n".join(lines) + "\n", encoding="utf-8")


ENTRY_PH = {
    "key": "soil.ph",
    "value": 6.62,
    "unit": "-",
    "confidence": "medium",
    "source": "J. Hazard. Mater. 2025, 499:140030, Table S4",
}


# ------------------------------------------------------- 二级：田块实测


def test_local_values_are_recorded_as_tier_two(tmp_path: Path) -> None:
    config = build_case(tmp_path / "case", elements=("Cd",))
    result = run_from_config(config)

    records = read_parameters(result.parameters_path)
    assert records, "数据齐全时溯源表也必须有内容"
    assert {row["tier"] for row in records} == {"2"}
    assert {row["tier_label"] for row in records} == {"local-measured"}
    assert {row["key"] for row in records} == {
        "soil.bulk_density",
        "soil.ph",
        "soil.organic_matter",
        "soil.clay",
        "soil.cec",
        "soil.initial.Cd",
    }


def test_local_source_points_at_the_row(tmp_path: Path) -> None:
    config = build_case(tmp_path / "case", elements=("Cd",))
    result = run_from_config(config)

    ph = next(
        row for row in read_parameters(result.parameters_path) if row["key"] == "soil.ph"
    )
    assert ph["source"].endswith("soil.csv:2")
    assert ph["unit"] == "1"


# ------------------------------------------------- 四级：回落到文献默认


def test_missing_cell_falls_back_to_library(tmp_path: Path) -> None:
    config = build_case(tmp_path / "case", elements=("Cd",))
    blank_cell(config, "ph")
    attach_library(config, [ENTRY_PH])

    result = run_from_config(config)
    records = read_parameters(result.parameters_path)
    ph = next(row for row in records if row["key"] == "soil.ph")

    assert ph["tier"] == "4"
    assert ph["tier_label"] == "literature-default"
    assert float(ph["value"]) == pytest.approx(6.62)
    assert "Table S4" in ph["source"]
    assert ph["confidence"] == "medium"


def test_local_value_wins_over_library(tmp_path: Path) -> None:
    config = build_case(tmp_path / "case", elements=("Cd",))
    attach_library(config, [ENTRY_PH | {"value": 99.0}])

    result = run_from_config(config)
    ph = next(
        row for row in read_parameters(result.parameters_path) if row["key"] == "soil.ph"
    )
    assert ph["tier"] == "2"
    assert float(ph["value"]) != pytest.approx(99.0)


# --------------------------------------------------------- 五级：报错


def test_missing_everywhere_raises_with_location(tmp_path: Path) -> None:
    config = build_case(tmp_path / "case", elements=("Cd",))
    blank_cell(config, "ph")

    with pytest.raises(ParameterSourceError) as excinfo:
        run_from_config(config)

    message = str(excinfo.value)
    assert "soil.ph" in message
    assert "soil.csv:2" in message
    assert "docs/parameters.md" in message
    assert "处置" in message
    assert "文献默认" in message  # 已尝试链里要说明试过哪几级


def test_entry_without_source_is_treated_as_missing(tmp_path: Path, caplog) -> None:
    config = build_case(tmp_path / "case", elements=("Cd",))
    blank_cell(config, "ph")
    attach_library(config, [ENTRY_PH | {"source": "   "}])

    with caplog.at_level(logging.WARNING, logger="hmfms.parameters.library"):
        with pytest.raises(ParameterSourceError):
            run_from_config(config)

    assert "来源为空" in caplog.text


# --------------------------------------------------------- 可复现性


def test_provenance_file_is_byte_identical_across_runs(tmp_path: Path) -> None:
    config = build_case(tmp_path / "case", elements=("Cd",))
    blank_cell(config, "ph")
    attach_library(config, [ENTRY_PH])

    first = run_from_config(config).parameters_path.read_bytes()
    second = run_from_config(config).parameters_path.read_bytes()
    assert first == second


def test_provenance_columns_are_complete(tmp_path: Path) -> None:
    config = build_case(tmp_path / "case", elements=("Cd",))
    result = run_from_config(config)

    header = result.parameters_path.read_text(encoding="utf-8").splitlines()[1]
    assert header.split(",") == [
        "key",
        "value",
        "unit",
        "dimension",
        "tier",
        "tier_label",
        "source",
        "confidence",
    ]


# ------------------------------------------- 解析器单测：一级与三级


def _entry(key: str, value: float, unit: str = "-") -> ParameterEntry:
    return ParameterEntry(
        key=key,
        value=value,
        unit=unit,
        source=f"test source for {key}",
        confidence="low",
        applicability=None,
    )


def test_event_override_beats_everything() -> None:
    resolver = ParameterResolver({"k": _entry("k", 1.0)})
    value = resolver.resolve(
        ParameterRequest(
            key="k",
            unit="-",
            dimension="dimensionless",
            doc_section="test",
            context="unit test",
            event=9.0,
            event_source="fertilizer_events.csv:12",
            local=5.0,
            local_source="soil.csv:2",
        )
    )
    assert value == pytest.approx(9.0)
    record = resolver.records()[-1]
    assert record.tier == 1
    assert record.tier_label == "event-override"
    assert record.source == "fertilizer_events.csv:12"


def test_selected_library_entry_beats_default() -> None:
    resolver = ParameterResolver({"k": _entry("k", 1.0), "k.alt": _entry("k.alt", 4.0)})
    value = resolver.resolve(
        ParameterRequest(
            key="k",
            unit="-",
            dimension="dimensionless",
            doc_section="test",
            context="unit test",
            selected="k.alt",
        )
    )
    assert value == pytest.approx(4.0)
    assert resolver.records()[-1].tier == 3


def test_selected_entry_missing_reports_that_key() -> None:
    resolver = ParameterResolver({"k": _entry("k", 1.0)})
    with pytest.raises(ParameterSourceError) as excinfo:
        resolver.resolve(
            ParameterRequest(
                key="k",
                unit="-",
                dimension="dimensionless",
                doc_section="test",
                context="unit test",
                selected="does.not.exist",
            )
        )
    assert "does.not.exist" in str(excinfo.value)


def test_library_unit_is_converted() -> None:
    """库里写 g/cm3，模型内部用 kg/m3。"""
    resolver = ParameterResolver(
        {"soil.bulk_density": _entry("soil.bulk_density", 1.3, "g/cm3")}
    )
    value = resolver.resolve(
        ParameterRequest(
            key="soil.bulk_density",
            unit="kg/m3",
            dimension="bulk_density",
            doc_section="test",
            context="unit test",
        )
    )
    assert value == pytest.approx(1300.0, rel=1e-12)


# ------------------------------------------------------- 库文件校验


def test_duplicate_library_key_rejected(tmp_path: Path) -> None:
    library = tmp_path / "params.yaml"
    library.write_text(
        yaml.safe_dump(
            {
                "parameters": [
                    {"key": "a", "value": 1, "unit": "-", "source": "s1"},
                    {"key": "a", "value": 2, "unit": "-", "source": "s2"},
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="重复"):
        load_library(library)