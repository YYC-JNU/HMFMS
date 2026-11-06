"""溶解态输移的公共部分：参数键与孔隙水浓度。

淋溶（式 20 的水量项）与径流溶解态共用同一个 Kd 与 `f_exch`：

```text
C_sol = f_exch · C_total / Kd
```

Kd 是**元素属性**，不是过程属性；同一元素只能有一个 `kd_basis`
（ADR-0011 / ADR-0012）。键名因此不带过程前缀。
"""

from __future__ import annotations


def kd_key(element: str) -> str:
    """参数库中的键：``kd.<元素>``（单位 L·kg⁻¹）。"""
    return f"kd.{element}"


def f_exch_key(element: str) -> str:
    """参数库中的键：``f_exch.<元素>``（无量纲，BCR F1 口径）。"""
    return f"f_exch.{element}"


def pore_water_concentration(
    *,
    concentration_mg_kg: float,
    f_exch: float,
    kd_l_per_kg: float,
) -> float:
    """土壤总含量 → 孔隙水浓度，mg·L⁻¹。

    `total` 口径时 `f_exch = 1`；`labile` 口径时 `f_exch` 是可交换态占比。
    """
    if kd_l_per_kg <= 0.0:
        raise ValueError(f"Kd 必须为正，收到 {kd_l_per_kg!r} L/kg")
    if not 0.0 < f_exch <= 1.0:
        raise ValueError(
            f"f_exch 必须在 (0, 1] 内，收到 {f_exch!r}；"
            "它是可交换态占全量的比例，不是百分数。"
        )
    return f_exch * concentration_mg_kg / kd_l_per_kg