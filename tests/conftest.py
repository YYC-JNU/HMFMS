"""测试夹具：在临时目录里造一份最小算例。

所有测试都通过**运行接缝**（配置文件进、CSV 出）验证行为，
不触碰内部函数（SPEC 的 Test Decisions）。
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

MODEL_TEMPLATE = """\
name: test-model
version: "0.1.0"

time:
  start: "{start}"
  end: "{end}"
  timestep: "{timestep}"

elements:
{elements}

field: field.yaml

inputs:
  soil:
    file: soil.csv
    units:
      depth: cm
      bulk_density: kg/m3
      concentration: mg/kg

output:
  directory: output
  interval:
    - daily

closure_tolerance: 1.0e-8
"""

FIELD_TEMPLATE = """\
field_id: test_field
area_ha: 1.0
location:
  latitude: 26.58
  longitude: 111.85
"""


def build_case(
    directory: Path,
    *,
    elements: tuple[str, ...] = ("Cd",),
    start: str = "2020-01-01",
    end: str = "2020-01-05",
    timestep: str = "1D",
    bulk_density: float = 1300.0,
    top: float = 0.0,
    bottom: float = 20.0,
    initials: dict[str, float] | None = None,
) -> Path:
    """写出配置文件并返回主配置路径。"""
    directory.mkdir(parents=True, exist_ok=True)
    initials = initials or {element: 0.35 for element in elements}

    element_lines = "".join(f"  - {element}\n" for element in elements)
    (directory / "model.yaml").write_text(
        MODEL_TEMPLATE.format(
            start=start,
            end=end,
            timestep=timestep,
            elements=element_lines,
        ),
        encoding="utf-8",
    )
    (directory / "field.yaml").write_text(FIELD_TEMPLATE, encoding="utf-8")

    header = (
        "layer_id,top,bottom,bulk_density,ph,organic_matter,clay,cec,"
        + ",".join(f"initial_{element}" for element in elements)
    )
    values = (
        f"1,{top},{bottom},{bulk_density},6.5,2.5,12.0,193,"
        + ",".join(str(initials[element]) for element in elements)
    )
    (directory / "soil.csv").write_text(
        dedent(header + "\n" + values + "\n"), encoding="utf-8"
    )
    return directory / "model.yaml"


@pytest.fixture
def minimal_case(tmp_path: Path) -> Path:
    """两个元素、两个不同的初始值——顺便证明它们不会串味。"""
    return build_case(
        tmp_path / "case",
        elements=("Cd", "Pb"),
        initials={"Cd": 0.35, "Pb": 25.0},
    )