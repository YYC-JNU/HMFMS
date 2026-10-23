"""NetCDF 沉降适配器。

职责只有一件事：把 WRF-EMEP 一类的格点 NetCDF **按田块坐标最近邻提取**
成一个单点 CSV，主循环随后只面对 CSV（`io/deposition.py`）。

三条纪律：

* 维度名、变量名、缓存路径全部来自配置，代码里不出现 ``time`` / ``lat`` /
  ``lon`` 等具体命名（ADR 的 X7）。
* 时间轴只接受日尺度；同一日期出现多步直接报错，不做隐式平均。
* 缓存文件写**源单位、round-trip 精度**，因此与直接提供的 CSV 加载后得到
  完全相同的数值。
"""

from __future__ import annotations

import csv
import logging
import math
from datetime import date
from pathlib import Path

import netCDF4
import numpy as np

from hmfms.config.models import DepositionConfig, NetCDFSourceConfig

logger = logging.getLogger(__name__)


def _axis_values(
    dataset: netCDF4.Dataset, name: str, dimension: str, source: Path
) -> list[float]:
    variable = dataset.variables[name]
    if tuple(variable.dimensions) != (dimension,):
        raise ValueError(
            f"{source} 变量 {name!r} 的维度 {tuple(variable.dimensions)} "
            f"必须是一维 {dimension!r}。"
        )
    values = [float(value) for value in np.ma.filled(variable[:], np.nan)]
    if not values:
        raise ValueError(f"{source} 变量 {name!r} 为空")
    if any(not math.isfinite(value) for value in values):
        raise ValueError(f"{source} 变量 {name!r} 含非有限坐标值")
    return values


def _nearest_index(values: list[float], target: float) -> int:
    return min(range(len(values)), key=lambda index: abs(values[index] - target))


def _target_longitude(longitude: float, lons: list[float]) -> float:
    """兼容 0–360 与 -180–180 两种经度约定。"""
    lon_min, lon_max = min(lons), max(lons)
    if longitude < lon_min and lon_max > 180.0 and longitude + 360.0 <= lon_max:
        return longitude + 360.0
    if longitude > lon_max and lon_min < 0.0 and longitude - 360.0 >= lon_min:
        return longitude - 360.0
    return longitude


def _value_at(
    dataset: netCDF4.Dataset,
    *,
    name: str,
    netcdf_spec: NetCDFSourceConfig,
    time_index: int,
    lat_index: int,
    lon_index: int,
    source: Path,
    day: date,
) -> float:
    variable = dataset.variables[name]
    dims = tuple(variable.dimensions)
    expected = {
        netcdf_spec.time_dim,
        netcdf_spec.lat_dim,
        netcdf_spec.lon_dim,
    }
    if len(dims) != 3 or set(dims) != expected:
        raise ValueError(
            f"{source} 变量 {name!r} 的维度 {dims} 必须恰好是 "
            f"{sorted(expected)} 三个维度（顺序不限）。"
        )
    selection = tuple(
        time_index
        if dim == netcdf_spec.time_dim
        else lat_index
        if dim == netcdf_spec.lat_dim
        else lon_index
        for dim in dims
    )
    raw = variable[selection]
    if np.ma.is_masked(raw):
        raise ValueError(
            f"{source} 变量 {name!r} 在 {day} 被掩码（fill value）。"
            "请先补全数据，或改用直接 CSV 并显式声明缺失。"
        )
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError(f"{source} 变量 {name!r} 在 {day} 不是有限数值")
    if value < 0.0:
        raise ValueError(
            f"{source} 变量 {name!r} 在 {day} 为负值 {value}；沉降通量不得为负"
        )
    return value


def extract_deposition_csv(
    source: Path,
    cache: Path,
    spec: DepositionConfig,
    elements: tuple[str, ...],
    latitude: float,
    longitude: float,
) -> Path:
    """按田块坐标提取单点日序列，写到 ``cache`` 并返回该路径。"""
    netcdf_spec = spec.netcdf
    if netcdf_spec is None:  # 配置层已保证，这里仅作类型收窄
        raise ValueError("format: netcdf 但缺少 netcdf 配置块")

    if not source.is_file():
        raise FileNotFoundError(f"NetCDF 沉降文件不存在：{source}")
    if cache.resolve() == source.resolve():
        raise ValueError(
            f"NetCDF 缓存路径不能与源文件相同：{cache}。"
            "请把 netcdf.cache 指到一个独立的 CSV。"
        )

    with netCDF4.Dataset(source, "r") as dataset:
        dims = (netcdf_spec.time_dim, netcdf_spec.lat_dim, netcdf_spec.lon_dim)
        missing_dims = [name for name in dims if name not in dataset.dimensions]
        if missing_dims:
            raise ValueError(
                f"{source} 缺少维度 {missing_dims}；"
                f"实际维度 {list(dataset.dimensions)}"
            )

        dry_variables = [spec.dry_columns[element] for element in elements]
        wet_variables = [spec.wet_columns[element] for element in elements]
        needed = [
            netcdf_spec.time_variable,
            netcdf_spec.latitude_variable,
            netcdf_spec.longitude_variable,
            *dry_variables,
            *wet_variables,
        ]
        missing_vars = sorted(
            {name for name in needed if name not in dataset.variables}
        )
        if missing_vars:
            raise ValueError(
                f"{source} 缺少变量 {missing_vars}；"
                f"实际变量 {list(dataset.variables)}"
            )

        lats = _axis_values(
            dataset, netcdf_spec.latitude_variable, netcdf_spec.lat_dim, source
        )
        lons = _axis_values(
            dataset, netcdf_spec.longitude_variable, netcdf_spec.lon_dim, source
        )

        target_lon = _target_longitude(longitude, lons)
        lat_min, lat_max = min(lats), max(lats)
        lon_min, lon_max = min(lons), max(lons)
        if not (lat_min <= latitude <= lat_max) or not (
            lon_min <= target_lon <= lon_max
        ):
            raise ValueError(
                f"请求坐标 (lat={latitude}, lon={longitude}) 超出 {source} 的网格范围："
                f"lat [{lat_min}, {lat_max}]，lon [{lon_min}, {lon_max}]"
            )

        lat_index = _nearest_index(lats, latitude)
        lon_index = _nearest_index(lons, target_lon)

        time_variable = dataset.variables[netcdf_spec.time_variable]
        if tuple(time_variable.dimensions) != (netcdf_spec.time_dim,):
            raise ValueError(
                f"{source} 时间变量 {netcdf_spec.time_variable!r} 的维度 "
                f"{tuple(time_variable.dimensions)} 必须是一维 "
                f"{netcdf_spec.time_dim!r}。"
            )
        units = netcdf_spec.time_units or getattr(time_variable, "units", None)
        if not units:
            raise ValueError(
                f"{source} 时间变量 {netcdf_spec.time_variable!r} 没有 units 属性；"
                "请在 inputs.deposition.netcdf.time_units 中给出"
                "（例如 'days since 2020-01-01'）。"
            )
        calendar = getattr(time_variable, "calendar", "standard")
        try:
            decoded = netCDF4.num2date(
                time_variable[:],
                units,
                calendar=calendar,
                only_use_cftime_datetimes=False,
            )
        except Exception as exc:  # 第三方解码错误转成带文件与配置的失败
            raise ValueError(
                f"{source} 无法解析时间轴（units={units!r}, "
                f"calendar={calendar!r}）：{exc}"
            ) from exc

        days: list[date] = []
        seen: dict[date, int] = {}
        for index, value in enumerate(decoded):
            day = date(value.year, value.month, value.day)
            if day in seen:
                raise ValueError(
                    f"{source} 的时间轴在同一日期 {day} 出现多步"
                    f"（下标 {seen[day]} 与 {index}）；v1 只接受日尺度时间轴。"
                )
            seen[day] = index
            days.append(day)

        rows: list[tuple[date, dict[str, float]]] = []
        for time_index, day in enumerate(days):
            values: dict[str, float] = {}
            for element in elements:
                for kind, mapping in (
                    ("dry", spec.dry_columns),
                    ("wet", spec.wet_columns),
                ):
                    values[f"{kind}:{element}"] = _value_at(
                        dataset,
                        name=mapping[element],
                        netcdf_spec=netcdf_spec,
                        time_index=time_index,
                        lat_index=lat_index,
                        lon_index=lon_index,
                        source=source,
                        day=day,
                    )
            rows.append((day, values))

    rows.sort(key=lambda item: item[0])
    cache.parent.mkdir(parents=True, exist_ok=True)
    columns = [spec.date_column, *dry_variables, *wet_variables]
    with cache.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for day, values in rows:
            writer.writerow(
                [
                    day.isoformat(),
                    *(repr(values[f"dry:{element}"]) for element in elements),
                    *(repr(values[f"wet:{element}"]) for element in elements),
                ]
            )

    logger.info(
        "NetCDF 沉降已提取：%s → %s（最近邻 lat=%s, lon=%s，%d 天）",
        source,
        cache,
        lats[lat_index],
        lons[lon_index],
        len(rows),
    )
    return cache