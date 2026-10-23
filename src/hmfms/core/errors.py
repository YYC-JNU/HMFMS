"""模型异常层次。

所有异常都携带足以定位问题的上下文（ADR-0008）。基类便于调用方
一次性捕获"这是模型自己抛出的、有明确原因的错误"。
"""

from __future__ import annotations


class HmfmsError(Exception):
    """所有模型自定义异常的基类。"""


class ParameterSourceError(HmfmsError):
    """参数缺失，或库条目的来源为空而等同缺失（ADR-0008）。

    错误信息必须同时给出四件事：参数键、谁要求它、依据文档哪一节、
    以及至少两条可执行的处置建议。
    """

    def __init__(
        self,
        *,
        key: str,
        context: str,
        doc_section: str,
        suggestions: tuple[str, ...] = (),
        tried: tuple[str, ...] = (),
    ) -> None:
        self.key = key
        self.context = context
        self.doc_section = doc_section
        self.suggestions = suggestions
        self.tried = tried
        super().__init__(self._render())

    def _render(self) -> str:
        lines = [f"ParameterSourceError: {self.key} 缺失"]
        lines.append(f"  来源需求: {self.context}")
        lines.append(f"  依据:     {self.doc_section}")
        if self.tried:
            lines.append("  已尝试:   " + " / ".join(self.tried))
        if self.suggestions:
            lines.append("  处置:")
            for suggestion in self.suggestions:
                lines.append(f"    - {suggestion}")
        return "\n".join(lines)


class DuplicateEventError(HmfmsError):
    """同一天出现两条互斥事件（ADR-0008）。

    重复不是"两次作业"，是录入错误——单季只能收一次。错误必须给出
    文件、日期与两个行号，让人五秒内定位。
    """

    def __init__(
        self,
        *,
        kind: str,
        path: str,
        day: object,
        detail: str,
        first_line: int,
        second_line: int,
        suggestion: str,
    ) -> None:
        self.kind = kind
        self.path = path
        self.day = day
        self.detail = detail
        self.first_line = first_line
        self.second_line = second_line
        self.suggestion = suggestion
        super().__init__(self._render())

    def _render(self) -> str:
        return "\n".join(
            [
                f"DuplicateEventError: 同一天出现 2 条 {self.kind} 事件",
                f"  文件: {self.path}",
                f"  日期: {self.day}（{self.detail}）",
                f"  行号: {self.first_line}, {self.second_line}",
                f"  处置: {self.suggestion}",
            ]
        )


class FluxImbalanceError(HmfmsError):
    """储量即将变为负数，或当日通量无法与实际储量匹配。

    不裁剪为 0：负储量意味着某处通量算错了，裁剪只会把错误埋起来。
    """

    def __init__(
        self,
        *,
        day: object,
        element: str,
        layer_id: int,
        mass_before: float,
        flux_in: dict[str, float],
        flux_out: dict[str, float],
        events: tuple[str, ...] = (),
    ) -> None:
        self.day = day
        self.element = element
        self.layer_id = layer_id
        self.mass_before = mass_before
        self.flux_in = dict(flux_in)
        self.flux_out = dict(flux_out)
        self.events = events
        super().__init__(self._render())

    def _render(self) -> str:
        total_in = sum(self.flux_in.values())
        total_out = sum(self.flux_out.values())
        lines = [
            f"FluxImbalanceError: {self.day} 元素 {self.element} 土层 {self.layer_id}",
            f"  当日储量: {self.mass_before:.12g} mg/m2",
            f"  输入合计: {total_in:.12g} mg/m2/d",
        ]
        for name, value in sorted(self.flux_in.items()):
            lines.append(f"    + {name}: {value:.12g}")
        lines.append(f"  输出合计: {total_out:.12g} mg/m2/d")
        for name, value in sorted(self.flux_out.items()):
            lines.append(f"    - {name}: {value:.12g}")
        lines.append(f"  更新后储量: {self.mass_before + total_in - total_out:.12g}")
        if self.events:
            lines.append("  当日事件:")
            for line in self.events:
                lines.append(f"    - {line}")
        lines.append("  处置: 检查各分项通量的参数与单位；储量不得裁剪为 0")
        return "\n".join(lines)