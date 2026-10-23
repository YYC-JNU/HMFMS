"""T03 验收测试：沉降输入。

全部通过运行接缝验证（配置进、CSV 出）。
"""

from __future__ import annotations

import csv
import logging
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from hmfms.cli import run_from_config

SOIL_CONTENT = (
    "layer_id,top,bottom,bulk_density,ph,organic_matter,clay,cec,initial_Cd\n"
    "1,0,20,1300,6.5,2.5,12.0,193,0.35\n"
)
FIELD_CONTENT = (
    "field_id: dep_field\narea_ha: 1.0\n"
    "location:\n  latitude: 26.58\n  longitude: 111.85\n"
)


def make_case(
    base: Path,
    *,
    days: int = 3,
    dry: float = 0.001,
    wet: float = 0.0005,
    units: str = "mg/m2/day",
    gap_policy: str = "error",
    magnitude: dict | None = None,
    rows: list[tuple[str, float, float]] | None = None,
    header: str = "date,dry_Cd,wet_Cd",
) -> Path:
    base.mkdir(parents=True, exist_ok=True)

    if rows is None:
        start = date(2020, 1, 1)
        rows = [
            ((start + timedelta(days=i)).isoformat(), dry, wet) for i in range(days)
        ]
        end = (start + timedelta(days=days - 1)).isoformat()
    else:
        end = rows[-1][0]

    (base / "soil.csv").write_text(SOIL_CONTENT, encoding="utf-8")
    (base / "field.yaml").write_text(FIELD_CONTENT, encoding="utf-8")
    body = "\n".join(f"{d},{a},{b}" for d, a, b in rows)
    (base / "deposition.csv").write_text(
        f"{header}\n{body}\n", encoding="utf-8"
    )

    deposition: dict = {
        "file": "deposition.csv",
        "units": units,
        "date_column": "date",
        "dry_columns": {"Cd": "dry_Cd"},
        "wet_columns": {"Cd": "wet_Cd"},
        "gap_policy": gap_policy,
    }
    if magnitude is not None:
        deposition["magnitude_check"] = {"Cd": magnitude}

    config = {
        "name": "dep-test",
        "version": "0.1.0",
        "time": {"start": rows[0][0], "end": end, "timestep": "1D"},
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
            "deposition": deposition,
        },
        "output": {"directory": "output", "interval": ["daily"]},
    }
    path = base / "model.yaml"
    path.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return path


def read_daily(path: Path) -> list[dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return list(csv.DictReader(lines[1:]))


def start_mass() -> float:
    """0.35 mg/kg × (1300 kg/m3 × 0.2 m)。"""
    return 0.35 * (1300.0 * 0.2)


# ------------------------------------------------------------- 守恒


def test_single_deposition_input_conserves(tmp_path: Path) -> None:
    config = make_case(tmp_path, days=3, dry=0.001, wet=0.0005)
    result = run_from_config(config)

    deposited = 3 * (0.001 + 0.0005)
    assert result.state.total_mass("Cd") - start_mass() == pytest.approx(
        deposited, rel=1e-12
    )


def test_cumulative_input_matches_soil_change(tmp_path: Path) -> None:
    config = make_case(tmp_path, days=4, dry=0.0008, wet=0.0004)
    result = run_from_config(config)
    last = read_daily(result.output_path)[-1]

    cumulative = float(last["cumulative_input_Cd_mg_m2"])
    mass = float(last["soil_mass_Cd_L1_mg_m2"])
    assert cumulative == pytest.approx(4 * 0.0012, rel=1e-12)
    assert mass - start_mass() == pytest.approx(cumulative, rel=1e-12)


def test_closure_stays_within_float_noise(tmp_path: Path) -> None:
    """有通量后残差不再恒为 0，而是浮点累加误差（量级 1e-15）。

    ``start + Σin`` 与 ``(((start + d1) + d2) + …)`` 的结合次序不同，
    结果必然差最后几位。这是 IEEE-754 的性质，不是模型误差——
    真正的判断标准是**相对**残差远低于 1e-8。
    """
    config = make_case(tmp_path, days=5)
    result = run_from_config(config)

    for row in read_daily(result.output_path):
        residual = float(row["closure_error_Cd_mg_m2"])
        mass = float(row["soil_mass_Cd_L1_mg_m2"])
        assert abs(residual) < 1e-12
        assert abs(residual) / mass < 1e-12


# --------------------------------------------------- 干 / 湿 / 总 自洽


def test_dry_plus_wet_equals_total(tmp_path: Path) -> None:
    config = make_case(tmp_path, days=3, dry=0.0013, wet=0.0007)
    result = run_from_config(config)

    for row in read_daily(result.output_path):
        dry = float(row["deposition_dry_Cd_mg_m2_d"])
        wet = float(row["deposition_wet_Cd_mg_m2_d"])
        total = float(row["deposition_Cd_mg_m2_d"])
        assert dry == pytest.approx(0.0013, rel=1e-12)
        assert wet == pytest.approx(0.0007, rel=1e-12)
        assert dry + wet == pytest.approx(total, rel=1e-12)


def test_drivers_expose_only_deposition(tmp_path: Path) -> None:
    """INV-1：驱动里只有沉降，没有降雨。"""
    config = make_case(tmp_path, days=2)
    result = run_from_config(config)
    assert set(result.records[0].drivers) == {"deposition_dry", "deposition_wet"}


# ------------------------------------------------------------- 缺口处理


def test_missing_day_under_error_policy_names_the_date(tmp_path: Path) -> None:
    rows = [("2020-01-01", 0.001, 0.0), ("2020-01-03", 0.003, 0.0)]
    config = make_case(tmp_path, rows=rows, gap_policy="error")

    with pytest.raises(ValueError) as excinfo:
        run_from_config(config)
    message = str(excinfo.value)
    assert "2020-01-02" in message
    assert "gap_policy=error" in message


def test_missing_day_under_interpolate_policy_is_linear(tmp_path: Path) -> None:
    rows = [("2020-01-01", 0.001, 0.0), ("2020-01-03", 0.003, 0.0)]
    config = make_case(tmp_path, rows=rows, gap_policy="interpolate")

    result = run_from_config(config)
    middle = read_daily(result.output_path)[1]
    assert float(middle["deposition_Cd_mg_m2_d"]) == pytest.approx(0.002, rel=1e-12)


def test_gap_is_never_silently_zero(tmp_path: Path) -> None:
    """缺日不得被当作零通量——守恒量必须只来自文件里的数。"""
    rows = [("2020-01-01", 0.002, 0.0), ("2020-01-03", 0.002, 0.0)]
    config = make_case(tmp_path, rows=rows, gap_policy="interpolate")
    result = run_from_config(config)

    total = result.state.total_mass("Cd") - start_mass()
    assert total == pytest.approx(0.006, rel=1e-12)  # 不是 0.004


# ------------------------------------------------------------- 量级检查


def test_out_of_range_flux_warns_with_hint(tmp_path: Path, caplog) -> None:
    config = make_case(
        tmp_path,
        days=2,
        dry=0.01,
        wet=0.0005,
        magnitude={"min": 0.0, "max": 0.001},
    )
    with caplog.at_level(logging.WARNING, logger="hmfms.io.deposition"):
        run_from_config(config)

    text = caplog.text
    assert "超出配置区间" in text
    assert "单位" in text


def test_within_range_does_not_warn(tmp_path: Path, caplog) -> None:
    config = make_case(
        tmp_path, days=2, dry=0.0008, wet=0.0004, magnitude={"min": 0.0, "max": 0.1}
    )
    with caplog.at_level(logging.WARNING, logger="hmfms.io.deposition"):
        run_from_config(config)
    assert "超出配置区间" not in caplog.text


# ------------------------------------------------------------- 拒绝坏数据


def test_unknown_unit_rejected_by_config(tmp_path: Path) -> None:
    config = make_case(tmp_path, days=2, units="kg/ha/fortnight")
    with pytest.raises(ValidationError) as excinfo:
        run_from_config(config)
    assert "kg/ha/fortnight" in str(excinfo.value)


def test_missing_column_names_the_column(tmp_path: Path) -> None:
    config = make_case(tmp_path, days=2, header="date,dry_Cd")
    with pytest.raises(ValueError) as excinfo:
        run_from_config(config)
    assert "wet_Cd" in str(excinfo.value)


def test_negative_flux_rejected(tmp_path: Path) -> None:
    rows = [("2020-01-01", -0.001, 0.0), ("2020-01-02", 0.001, 0.0)]
    config = make_case(tmp_path, rows=rows)
    with pytest.raises(ValueError, match="不得为负"):
        run_from_config(config)


def test_duplicate_date_rejected(tmp_path: Path) -> None:
    rows = [("2020-01-01", 0.001, 0.0), ("2020-01-01", 0.001, 0.0)]
    config = make_case(tmp_path, rows=rows)
    with pytest.raises(ValueError, match="重复"):
        run_from_config(config)


# ------------------------------------------------------------- 单位换算


def test_values_converted_from_declared_unit(tmp_path: Path) -> None:
    """3.65 g·ha⁻¹·a⁻¹ = 0.365 mg·m⁻²·a⁻¹ ≈ 0.001 mg·m⁻²·d⁻¹。"""
    config = make_case(
        tmp_path, days=2, dry=3.65, wet=0.0, units="g/ha/yr"
    )
    result = run_from_config(config)
    first = read_daily(result.output_path)[0]
    assert float(first["deposition_Cd_mg_m2_d"]) == pytest.approx(0.001, rel=1e-9)