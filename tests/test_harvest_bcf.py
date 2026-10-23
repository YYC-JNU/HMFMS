"""T08 验收测试：作物收获的 BCF 模式与模式互斥。

绝大多数用例走**运行接缝**（配置进、CSV 出）。
只有 ``depends_on_state`` 标志本身走模块接缝——它是 ADR-0007 要求的
显式声明，测试需要直接读到它，而不是靠行为反推。
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
import yaml

from hmfms.cli import run_from_config
from hmfms.core.errors import ParameterSourceError
from hmfms.schemes.harvest import HarvestModule

FIELD = "field_id: f\narea_ha: 1.0\nlocation:\n  latitude: 26.5\n  longitude: 111.8\n"
SOIL_MASS = 1300.0 * 0.2

LIBRARY = [
    {
        "key": "bcf.rice.grain.Cd",
        "value": 0.10,
        "unit": "-",
        "confidence": "medium",
        "source": "PARAMS_SOURCE §7.2（田间区间与 P3 中值的折中）",
    },
    {
        "key": "bcf.rice.straw.Cd",
        "value": 0.20,
        "unit": "-",
        "confidence": "low",
        "source": "demo value for tests",
    },
    {
        "key": "crop.rice.grain.Pb",
        "value": 0.05,
        "unit": "mg/kg",
        "source": "demo value for tests",
    },
    {
        "key": "crop.rice.straw.Pb",
        "value": 0.6,
        "unit": "mg/kg",
        "source": "demo value for tests",
    },
]

HARVEST_COLUMNS = [
    "event_id",
    "date",
    "crop_id",
    "organ",
    "yield",
    "removal_fraction",
    "Cd_bcf_override",
    "Cd_mg_kg_override",
    "Pb_mg_kg_override",
]


def _write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def make_case(
    base: Path,
    *,
    events: list[dict],
    cd_initial: float = 0.35,
    modes: dict[str, object] | None = None,
    basis: str | None = None,
    library: list[dict] | None = None,
    days: int = 4,
) -> Path:
    """``modes``：元素 → 模式（字符串或字符串列表），写成 ``elements/<E>.yaml``。"""
    base.mkdir(parents=True, exist_ok=True)
    (base / "soil.csv").write_text(
        "layer_id,top,bottom,bulk_density,ph,organic_matter,clay,cec,"
        "initial_Cd,initial_Pb\n"
        f"1,0,20,1300,6.5,2.5,12.0,193,{cd_initial},25.0\n",
        encoding="utf-8",
    )
    (base / "field.yaml").write_text(FIELD, encoding="utf-8")
    _write_csv(base / "management" / "harvest_events.csv", HARVEST_COLUMNS, events)
    (base / "params.yaml").write_text(
        yaml.safe_dump(
            {"parameters": LIBRARY if library is None else library},
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    element_configs: dict[str, str] = {}
    for element, mode in (modes or {}).items():
        relative = f"elements/{element}.yaml"
        crop_uptake: dict[str, object] = {"mode": mode}
        if basis is not None:
            crop_uptake["basis"] = basis
        payload: dict[str, object] = {
            "element": element,
            "crop_uptake": crop_uptake,
        }
        path = base / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        element_configs[element] = relative

    config = {
        "name": "harvest-bcf-test",
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
                "units": {"yield_rate": "kg/ha", "concentration": "mg/kg"},
            }
        },
        "parameters": {"library": "params.yaml"},
        "output": {"directory": "output", "interval": ["daily"]},
    }
    if element_configs:
        config["element_configs"] = element_configs
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
    "date": "2020-01-02",
    "crop_id": "rice",
    "organ": "grain",
    "yield": "6500",
    "removal_fraction": "1.0",
}
STRAW = GRAIN | {"organ": "straw", "yield": "6700", "removal_fraction": "0.3"}
BCF_MODES = {"Cd": "bcf", "Pb": "concentration"}


# ------------------------------------------------------- 状态反馈的行为


def test_bcf_flux_scales_with_soil_concentration(tmp_path: Path) -> None:
    """土壤含量翻倍，收获通量必须翻倍（浓度模式不会这样）。"""
    low = make_case(tmp_path / "low", events=[GRAIN], cd_initial=0.35, modes=BCF_MODES)
    high = make_case(tmp_path / "high", events=[GRAIN], cd_initial=0.70, modes=BCF_MODES)

    low_flux = float(
        read_daily(run_from_config(low).output_path)[1]["harvest_Cd_mg_m2_d"]
    )
    high_flux = float(
        read_daily(run_from_config(high).output_path)[1]["harvest_Cd_mg_m2_d"]
    )

    expected_low = 6500 * 0.10 * 0.35 / 10_000.0
    assert low_flux == pytest.approx(expected_low, rel=1e-12)
    assert high_flux == pytest.approx(2.0 * expected_low, rel=1e-12)


def test_bcf_conservation_uses_recorded_removal(tmp_path: Path) -> None:
    """守恒用输出文件里的实际移除量验证，不靠线性叠加假设。"""
    config = make_case(tmp_path, events=[GRAIN], modes=BCF_MODES)
    result = run_from_config(config)
    rows = read_daily(result.output_path)

    recorded = sum(float(row["harvest_Cd_mg_m2_d"]) for row in rows)
    assert 0.35 * SOIL_MASS - result.state.total_mass("Cd") == pytest.approx(
        recorded, rel=1e-12
    )


def test_bcf_uses_the_day_start_snapshot_for_every_organ(tmp_path: Path) -> None:
    """同日两个器官都按当日起始含量计算，而不是先扣籽粒再算秸秆。"""
    config = make_case(tmp_path, events=[GRAIN, STRAW], modes=BCF_MODES)
    rows = read_daily(run_from_config(config).output_path)

    expected = (6500 * 0.10 + 6700 * 0.20 * 0.3) * 0.35 / 10_000.0
    assert float(rows[1]["harvest_Cd_mg_m2_d"]) == pytest.approx(expected, rel=1e-12)


def test_bcf_module_is_marked_as_state_dependent() -> None:
    assert HarvestModule(frozenset({"Cd"})).depends_on_state is True
    assert HarvestModule().depends_on_state is False


# ------------------------------------------------------------- 模式互斥


def test_conflicting_modes_rejected_with_element_name(tmp_path: Path) -> None:
    config = make_case(
        tmp_path,
        events=[GRAIN],
        modes={"Cd": ["concentration", "bcf"], "Pb": "concentration"},
    )

    with pytest.raises(ValueError) as excinfo:
        run_from_config(config)
    message = str(excinfo.value)
    assert "Cd" in message
    assert "concentration" in message and "bcf" in message


def test_unknown_mode_rejected(tmp_path: Path) -> None:
    config = make_case(tmp_path, events=[GRAIN], modes={"Cd": "bcf_ph"})

    with pytest.raises(ValueError, match="bcf_ph"):
        run_from_config(config)


def test_available_basis_points_at_unimplemented_doc(tmp_path: Path) -> None:
    config = make_case(tmp_path, events=[GRAIN], modes=BCF_MODES, basis="available")

    with pytest.raises(ValueError) as excinfo:
        run_from_config(config)
    message = str(excinfo.value)
    assert "available" in message
    assert "未实现" in message
    assert "equations.md" in message


# ------------------------------------------------------------- 参数溯源


def test_provenance_shows_the_mode_per_element(tmp_path: Path) -> None:
    config = make_case(tmp_path, events=[GRAIN], modes=BCF_MODES)
    records = read_parameters(run_from_config(config).parameters_path)
    by_key = {row["key"]: row for row in records}

    assert by_key["crop_uptake.mode.Cd"]["note"].startswith("bcf")
    assert by_key["crop_uptake.mode.Pb"]["note"].startswith("concentration")

    bcf = by_key["bcf.rice.grain.Cd"]
    assert bcf["tier"] == "3"
    assert float(bcf["value"]) == pytest.approx(0.10)
    assert bcf["unit"] == "1"

    assert by_key["crop.rice.grain.Pb"]["tier"] == "3"


def test_bcf_override_column_is_tier_one(tmp_path: Path) -> None:
    event = GRAIN | {"Cd_bcf_override": "0.5"}
    config = make_case(tmp_path, events=[event], modes=BCF_MODES)
    result = run_from_config(config)

    rows = read_daily(result.output_path)
    assert float(rows[1]["harvest_Cd_mg_m2_d"]) == pytest.approx(
        6500 * 0.5 * 0.35 / 10_000.0, rel=1e-12
    )

    record = next(
        row
        for row in read_parameters(result.parameters_path)
        if row["key"] == "bcf.rice.grain.Cd"
    )
    assert record["tier"] == "1"
    assert record["source"] == "harvest_events.csv:2"


def test_concentration_override_is_rejected_in_bcf_mode(tmp_path: Path) -> None:
    """覆盖列与当前模式不匹配时必须报错，不能静默忽略。"""
    event = GRAIN | {"Cd_mg_kg_override": "0.25"}
    config = make_case(tmp_path, events=[event], modes=BCF_MODES)

    with pytest.raises(ValueError) as excinfo:
        run_from_config(config)
    message = str(excinfo.value)
    assert "Cd_mg_kg_override" in message
    assert "bcf" in message


def test_missing_bcf_parameter_reports_key_and_location(tmp_path: Path) -> None:
    library = [entry for entry in LIBRARY if entry["key"] != "bcf.rice.grain.Cd"]
    config = make_case(tmp_path, events=[GRAIN], modes=BCF_MODES, library=library)

    with pytest.raises(ParameterSourceError) as excinfo:
        run_from_config(config)
    message = str(excinfo.value)
    assert "bcf.rice.grain.Cd" in message
    assert "harvest_events.csv:2" in message
    assert "parameters.md" in message


# ----------------------------------------------------------------- 端点


def test_zero_bcf_gives_zero_removal(tmp_path: Path) -> None:
    library = [
        entry | {"value": 0.0} if entry["key"] == "bcf.rice.grain.Cd" else entry
        for entry in LIBRARY
    ]
    config = make_case(tmp_path, events=[GRAIN], modes=BCF_MODES, library=library)
    result = run_from_config(config)

    rows = read_daily(result.output_path)
    assert all(float(row["harvest_Cd_mg_m2_d"]) == 0.0 for row in rows)
    assert result.state.total_mass("Cd") == pytest.approx(0.35 * SOIL_MASS, rel=1e-12)


def test_zero_yield_gives_zero_removal(tmp_path: Path) -> None:
    config = make_case(tmp_path, events=[GRAIN | {"yield": "0"}], modes=BCF_MODES)
    result = run_from_config(config)

    rows = read_daily(result.output_path)
    assert all(float(row["harvest_Cd_mg_m2_d"]) == 0.0 for row in rows)
    assert result.state.total_mass("Cd") == pytest.approx(0.35 * SOIL_MASS, rel=1e-12)