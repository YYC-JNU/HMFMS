"""日水量平衡。

式 17 需要下渗通量 ``q_leach``，式 20 需要径流量 ``Q_runoff``。两者都由
当日降水盈余分配而来：

```text
excess   = max(0, P + I − ET)
Q_runoff = f_runoff · excess
q_leach  = excess − Q_runoff
```

**边界（ADR-0012）**：这是集总式分配，不含土壤储水、优先流与降雨强度；
`f_runoff` 必须由用户给出，没有文献默认值。未配置时按 0 处理（全部下渗），
这是 T14/T15 的既有行为，但**启用径流时必须显式给出**。
"""

from __future__ import annotations


def _require_non_negative(**values: float) -> None:
    for name, value in values.items():
        if value < 0.0:
            raise ValueError(f"{name} 不得为负，收到 {value!r}")


def split_excess(
    *,
    precipitation_mm: float,
    irrigation_mm: float,
    evapotranspiration_mm: float,
    runoff_fraction: float,
) -> tuple[float, float]:
    """把降水盈余分成 (径流量, 下渗量)，单位均为 mm·d⁻¹。"""
    _require_non_negative(
        precipitation_mm=precipitation_mm,
        irrigation_mm=irrigation_mm,
        evapotranspiration_mm=evapotranspiration_mm,
    )
    if not 0.0 <= runoff_fraction <= 1.0:
        raise ValueError(
            f"runoff_fraction 必须在 [0, 1] 内，收到 {runoff_fraction!r}；"
            "它是地表径流占降水盈余的比例。"
        )
    excess = max(
        0.0, precipitation_mm + irrigation_mm - evapotranspiration_mm
    )
    runoff = runoff_fraction * excess
    return runoff, excess - runoff


def percolation_mm(
    *,
    precipitation_mm: float,
    irrigation_mm: float,
    evapotranspiration_mm: float,
) -> float:
    """当日下渗量，mm·d⁻¹（未扣地表径流的旧口径）。

    保留给只启用淋溶、没有配置 `hydrology.runoff_fraction` 的场景。
    """
    _, percolation = split_excess(
        precipitation_mm=precipitation_mm,
        irrigation_mm=irrigation_mm,
        evapotranspiration_mm=evapotranspiration_mm,
        runoff_fraction=0.0,
    )
    return percolation