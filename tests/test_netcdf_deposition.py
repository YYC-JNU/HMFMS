"""T11 验收测试：NetCDF 沉降适配器。

所有用例走**运行接缝**（配置 + 数据进，CSV 出）。NetCDF 的维度名、变量名
一律使用非标准命名（`t` / `y` / `x`），以证明适配器不假设任何具体命名。
"""

from __future__ import annotations

import csv
from pathlib import Path

import netCDF4
import numpy as np
import pytest
import yaml
from pydantic import ValidationError

from hmfms.cli import run_from_config

# netCDF4 1.7.4 在 NumPy 2.5 下写变量时会触发第三方弃用警告，与被测行为无关。
pytestmark = pytest.mark.filterwarnings(
    "ignore:Setting the shape on a NumPy array has been deprecated"
)

TIME_DIM = "t"
LAT_DIM = "y"
LON_DIM = "x"
TIME_VAR = "time"
LAT_VAR = "lat"
LON_VAR = "lon"
DRY_VAR = "dry_Cd"
WET_VAR = "wet_Cd"

LATS = [26.4, 27.4]
LONS = [111.7, 112.7]


def _read(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("#")
    reader = csv.DictReader(lines[1:])
    return list(reader.fieldnames or []), list(reader)


def _read_plain_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def write_netcdf(
    path: Path,
    *,
    lats: list[float] | None = None,
    lons: list[float] | None = None,
    days: int = 3,
    dry: np.ndarray | None = None,
    wet: np.ndarray | None = None,
    include_dry: bool = True,
) -> None:
    """写一个维度名、变量名都非标准的 NetCDF。"""
    lats = LATS if lats is None else lats
    lons = LONS if lons is None else lons

    if dry is None:
        # 每个格点取不同值，便于验证确实是"最近邻"而不是第一个格点。
        dry = np.zeros((days, len(lats), len(lons)))
        for ti in range(days):
            for yi in range(len(lats)):
                for xi in range(len(lons)):
                    dry[ti, yi, xi] = 0.001 * (ti + 1) + 0.01 * yi + 0.1 * xi
    if wet is None:
        wet = np.zeros((days, len(lats), len(lons)))

    with netCDF4.Dataset(path, "w") as dataset:
        dataset.createDimension(TIME_DIM, days)
        dataset.createDimension(LAT_DIM, len(lats))
        dataset.createDimension(LON_DIM, len(lons))

        time_var = dataset.createVariable(TIME_VAR, "f8", (TIME_DIM,))
        time_var.units = "days since 2020-01-01"
        time_var[:] = np.arange(days)

        lat_var = dataset.createVariable(LAT_VAR, "f8", (LAT_DIM,))
        lat_var[:] = lats
        lon_var = dataset.createVariable(LON_VAR, "f8", (LON_DIM,))
        lon_var[:] = lons

        if include_dry:
            dry_var = dataset.createVariable(
                DRY_VAR, "f8", (TIME_DIM, LAT_DIM, LON_DIM)
            )
            dry_var[:] = dry.tolist()
        wet_var = dataset.createVariable(WET_VAR, "f8", (TIME_DIM, LAT_DIM, LON_DIM))
        wet_var[:] = wet.tolist()


def netcdf_block(**overrides: object) -> dict:
    block: dict[str, object] = {
        "file": "grid.nc",
        "format": "netcdf",
        "units": "mg/m2/day",
        "date_column": "date",
        "dry_columns": {"Cd": DRY_VAR},
        "wet_columns": {"Cd": WET_VAR},
        "gap_policy": "error",
        "netcdf": {
            "cache": "extracted.csv",
            "time_dim": TIME_DIM,
            "lat_dim": LAT_DIM,
            "lon_dim": LON_DIM,
            "time_variable": TIME_VAR,
            "latitude_variable": LAT_VAR,
            "longitude_variable": LON_VAR,
        },
    }
    block.update(overrides)
    return block


def csv_block(**overrides: object) -> dict:
    block: dict[str, object] = {
        "file": "direct.csv",
        "format": "csv",
        "units": "mg/m2/day",
        "date_column": "date",
        "dry_columns": {"Cd": DRY_VAR},
        "wet_columns": {"Cd": WET_VAR},
        "gap_policy": "error",
    }
    block.update(overrides)
    return block


def make_case(
    base: Path,
    *,
    deposition: dict,
    latitude: float = 26.5,
    longitude: float = 111.8,
    interval: tuple[str, ...] = ("daily",),
    days: int = 3,
) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    (base / "field.yaml").write_text(
        "field_id: f\narea_ha: 1.0\nlocation:\n"
        f"  latitude: {latitude}\n  longitude: {longitude}\n",
        encoding="utf-8",
    )
    (base / "soil.csv").write_text(
        "layer_id,top,bottom,bulk_density,ph,organic_matter,clay,cec,initial_Cd\n"
        "1,0,20,1300,6.5,2.5,12.0,193,0.35\n",
        encoding="utf-8",
    )
    config = {
        "name": "netcdf-deposition-test",
        "version": "0.1.0",
        "time": {
            "start": "2020-01-01",
            "end": f"2020-01-{days:02d}",
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
            "deposition": deposition,
        },
        "output": {"directory": "output", "interval": list(interval)},
    }
    path = base / "model.yaml"
    path.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return path


# ------------------------------------------------------------- 最近邻提取


def test_extracts_the_nearest_grid_point(tmp_path: Path) -> None:
    base = tmp_path / "case"
    config = make_case(base, deposition=netcdf_block())
    write_netcdf(base / "grid.nc")

    result = run_from_config(config)
    _, rows = _read(result.output_paths["daily"])

    # 田块 (26.5, 111.8) 最近的是 (26.4, 111.7) 即 y=0 / x=0。
    assert [float(row["deposition_dry_Cd_mg_m2_d"]) for row in rows] == pytest.approx(
        [0.001, 0.002, 0.003], rel=1e-12
    )

    cache = base / "extracted.csv"
    assert cache.is_file()
    fields, cache_rows = _read_plain_csv(cache)
    assert fields == ["date", DRY_VAR, WET_VAR]
    assert [row["date"] for row in cache_rows] == [
        "2020-01-01",
        "2020-01-02",
        "2020-01-03",
    ]


def test_longitude_wrap_between_0_360_and_180(tmp_path: Path) -> None:
    """文件用 0–360，田块用 -180–180：应能匹配到同一根经线。"""
    base = tmp_path / "case"
    config = make_case(base, deposition=netcdf_block(), longitude=-170.0)
    write_netcdf(base / "grid.nc", lons=[190.0, 191.0])

    result = run_from_config(config)
    _, rows = _read(result.output_paths["daily"])
    assert [float(row["deposition_dry_Cd_mg_m2_d"]) for row in rows] == pytest.approx(
        [0.001, 0.002, 0.003], rel=1e-12
    )


# --------------------------------------------- 两条路径输出逐字节一致


def test_netcdf_and_direct_csv_paths_produce_identical_outputs(
    tmp_path: Path,
) -> None:
    interval = ("daily", "monthly", "annual")

    netcdf_dir = tmp_path / "netcdf"
    netcdf_config = make_case(
        netcdf_dir, deposition=netcdf_block(), interval=interval
    )
    write_netcdf(netcdf_dir / "grid.nc")
    from_netcdf = run_from_config(netcdf_config)

    csv_dir = tmp_path / "csv"
    csv_config = make_case(csv_dir, deposition=csv_block(), interval=interval)
    # 故意用与缓存不同的格式写"直接 CSV"，证明比较的是数值而不是文件字节。
    lines = ["date,dry_Cd,wet_Cd"]
    for index in range(3):
        lines.append(f"2020-01-{index + 1:02d},{0.001 * (index + 1):.9f},0.000000000")
    (csv_dir / "direct.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    from_csv = run_from_config(csv_config)

    assert from_netcdf.config_fingerprint == from_csv.config_fingerprint
    for frequency in interval:
        assert (
            from_netcdf.output_paths[frequency].read_bytes()
            == from_csv.output_paths[frequency].read_bytes()
        )


def test_extracted_cache_can_be_used_directly(tmp_path: Path) -> None:
    base = tmp_path / "case"
    netcdf_config = make_case(base, deposition=netcdf_block())
    write_netcdf(base / "grid.nc")
    first = run_from_config(netcdf_config)
    assert (base / "extracted.csv").is_file()

    csv_config = make_case(base, deposition=csv_block(file="extracted.csv"))
    second = run_from_config(csv_config)

    assert first.config_fingerprint == second.config_fingerprint
    assert (
        first.output_paths["daily"].read_bytes()
        == second.output_paths["daily"].read_bytes()
    )


def test_fingerprint_changes_with_deposition_values(tmp_path: Path) -> None:
    """指纹认的是有效沉降数值，不是文件路径或格式。"""

    def run_with(value: str) -> str:
        base = tmp_path / value
        config = make_case(base, deposition=csv_block())
        (base / "direct.csv").write_text(
            "date,dry_Cd,wet_Cd\n"
            f"2020-01-01,{value},0\n2020-01-02,{value},0\n2020-01-03,{value},0\n",
            encoding="utf-8",
        )
        return run_from_config(config).config_fingerprint

    assert run_with("0.001") != run_with("0.002")


# ------------------------------------------------------------- 错误信息


def test_coordinates_outside_the_grid_report_ranges(tmp_path: Path) -> None:
    base = tmp_path / "case"
    config = make_case(base, deposition=netcdf_block(), latitude=30.0)
    write_netcdf(base / "grid.nc")

    with pytest.raises(ValueError) as excinfo:
        run_from_config(config)

    message = str(excinfo.value)
    assert "30.0" in message
    assert "[26.4, 27.4]" in message
    assert "[111.7, 112.7]" in message


def test_missing_variable_lists_available_names(tmp_path: Path) -> None:
    base = tmp_path / "case"
    config = make_case(base, deposition=netcdf_block())
    write_netcdf(base / "grid.nc", include_dry=False)

    with pytest.raises(ValueError) as excinfo:
        run_from_config(config)

    message = str(excinfo.value)
    assert DRY_VAR in message
    assert WET_VAR in message
    assert TIME_VAR in message


def test_missing_dimension_reports_actual_dimensions(tmp_path: Path) -> None:
    block = netcdf_block()
    block["netcdf"] = dict(block["netcdf"], time_dim="missing_time")
    base = tmp_path / "case"
    config = make_case(base, deposition=block)
    write_netcdf(base / "grid.nc")

    with pytest.raises(ValueError) as excinfo:
        run_from_config(config)

    message = str(excinfo.value)
    assert "missing_time" in message
    assert TIME_DIM in message


# --------------------------------------------------------- 配置结构校验


def test_netcdf_block_is_required_for_netcdf_format(tmp_path: Path) -> None:
    block = netcdf_block()
    block.pop("netcdf")
    config = make_case(tmp_path / "case", deposition=block)

    with pytest.raises(ValidationError, match="netcdf"):
        run_from_config(config)


def test_netcdf_block_is_rejected_for_csv_format(tmp_path: Path) -> None:
    block = csv_block()
    block["netcdf"] = netcdf_block()["netcdf"]
    config = make_case(tmp_path / "case", deposition=block)

    with pytest.raises(ValidationError, match="netcdf"):
        run_from_config(config)