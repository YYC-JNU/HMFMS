"""守卫测试：源码里不得出现元素名与示例数值。

这两条对应 ``docs/HARDCODE_AUDIT.md`` §5。它们不测行为，只测**纪律**：
一旦有人在 ``src/`` 里写了 ``if element == "Cd"`` 或把示例容重写死，
测试立刻失败。

扫描时排除文档字符串：注释与文档里谈到 Cd、Pb 是正常的，
真正的问题是它们出现在**可执行代码**里。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "src" / "hmfms"

ELEMENT_SYMBOLS = frozenset({"Cd", "Pb", "As", "Cu", "Zn", "Hg", "Ni", "Cr"})

# docs/HARDCODE_AUDIT.md §3 里点名过的示例值：只能出现在 tests/ 或 examples/
MAGIC_SOIL_VALUES = frozenset({1300.0, 260.0, 0.35, 0.0027})


def _python_files() -> list[Path]:
    return sorted(SOURCE_ROOT.rglob("*.py"))


def _docstring_node_ids(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            continue
        body = getattr(node, "body", [])
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            ids.add(id(first.value))
    return ids


@pytest.mark.parametrize("path", _python_files(), ids=lambda p: p.name)
def test_no_element_symbols_in_executable_code(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = _docstring_node_ids(tree)
    offenders: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstrings:
                continue
            if node.value in ELEMENT_SYMBOLS:
                offenders.append(f"字符串 {node.value!r} (行 {node.lineno})")
        elif isinstance(node, ast.Name) and node.id in ELEMENT_SYMBOLS:
            offenders.append(f"标识符 {node.id!r} (行 {node.lineno})")
        elif isinstance(node, ast.Attribute) and node.attr in ELEMENT_SYMBOLS:
            offenders.append(f"属性 {node.attr!r} (行 {node.lineno})")

    assert not offenders, (
        f"{path.name} 的可执行代码里出现了元素名：{offenders}。"
        "元素名只能来自配置（ADR-0010）。"
    )


@pytest.mark.parametrize("path", _python_files(), ids=lambda p: p.name)
def test_no_example_soil_values_in_executable_code(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            if isinstance(node.value, bool):
                continue
            if float(node.value) in MAGIC_SOIL_VALUES:
                offenders.append(f"{node.value!r} (行 {node.lineno})")

    assert not offenders, (
        f"{path.name} 出现了示例土壤数值：{offenders}。"
        "它们只应出现在 tests/ 或 examples/。"
    )


def test_guard_covers_the_whole_package() -> None:
    """防止扫描路径写错导致守卫静默失效。"""
    names = {path.name for path in _python_files()}
    assert "units.py" in names
    assert "integrator.py" in names
    assert len(names) >= 6