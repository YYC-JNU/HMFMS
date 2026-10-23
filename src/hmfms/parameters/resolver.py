"""五级参数解析链（ADR-0010）。

优先级从高到低：

1. **事件级覆盖** —— 管理事件表里当行显式给出的值
2. **田块实测** —— 土壤剖面、田块配置里的实测值
3. **用户选择的库条目** —— 配置里点名要用的那一条
4. **文献默认** —— 库中该键的默认条目
5. **报错** —— 抛出 ``ParameterSourceError``，指明位置与依据

解析**集中发生一次**，结果记入溯源表；模块拿到的是数值，不是配置。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from hmfms.core.errors import ParameterSourceError
from hmfms.parameters.library import ParameterEntry
from hmfms.utils.units import to_internal

TIER_PLACEHOLDER = 0
TIER_EVENT = 1
TIER_LOCAL = 2
TIER_SELECTED = 3
TIER_LITERATURE = 4

TIER_CONFIG = TIER_LOCAL
TIER_LABEL_CONFIG = "local-config"

TIER_LABELS: Mapping[int, str] = {
    TIER_PLACEHOLDER: "PLACEHOLDER",
    TIER_EVENT: "event-override",
    TIER_LOCAL: "local-measured",
    TIER_SELECTED: "library-selected",
    TIER_LITERATURE: "literature-default",
}

INTERNAL_UNITS: Mapping[str, str] = {
    "flux": "mg/m2/day",
    "concentration": "mg/kg",
    "water_concentration": "mg/L",
    "water_depth": "mm",
    "application_rate": "kg/ha",
    "bulk_density": "kg/m3",
    "thickness": "m",
    "dimensionless": "1",
}

DEFAULT_SUGGESTIONS: tuple[str, ...] = (
    "在管理事件表的对应行补一个覆盖列",
    "在田块配置或土壤剖面中填入实测值",
    "在参数库中增补该条目，并写明来源",
)


@dataclass(frozen=True)
class ParameterRequest:
    """一次解析请求。

    ``event`` / ``local`` 的数值以 ``unit`` 为单位；库条目自带单位，
    两者都会被换算到 ``dimension`` 对应的内部单位。
    """

    key: str
    unit: str
    dimension: str
    doc_section: str
    context: str
    event: float | None = None
    event_source: str = ""
    local: float | None = None
    local_source: str = ""
    selected: str | None = None
    suggestions: tuple[str, ...] = DEFAULT_SUGGESTIONS


@dataclass(frozen=True)
class Provenance:
    key: str
    value: float | None
    internal_unit: str
    dimension: str
    tier: int
    tier_label: str
    source: str
    confidence: str
    note: str = ""


@dataclass
class ParameterResolver:
    """把请求解析成数值，并记录每个值的来龙去脉。"""

    library: Mapping[str, ParameterEntry]
    document: str = "docs/parameters.md"
    allow_placeholder: bool = False
    _records: list[Provenance] = field(default_factory=list)
    _placeholder_keys: set[str] = field(default_factory=set)

    def resolve(self, request: ParameterRequest) -> float:
        value, tier, source, confidence = self._pick(request)
        note = ""
        if tier == TIER_PLACEHOLDER:
            self._placeholder_keys.add(request.key)
            note = (
                f"{request.doc_section}；占位值 0，仅供结构演示，"
                "不得用于正式结论"
            )
        self._records.append(
            Provenance(
                key=request.key,
                value=value,
                internal_unit=INTERNAL_UNITS[request.dimension],
                dimension=request.dimension,
                tier=tier,
                tier_label=TIER_LABELS[tier],
                source=source,
                confidence=confidence,
                note=note,
            )
        )
        return value

    def records(self) -> tuple[Provenance, ...]:
        return tuple(self._records)

    @property
    def has_placeholders(self) -> bool:
        return bool(self._placeholder_keys)

    def placeholder_keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._placeholder_keys))

    def record_choice(
        self,
        *,
        key: str,
        choice: str,
        source: str,
        note: str = "",
    ) -> None:
        """记录一个**分类选择**（如吸收模式）。

        它没有数值，而 ``value`` 列保持数值语义——因此留空，选择本身写进
        ``note``。把模式编码成 0/1 会让溯源表看起来有数、实际不可读。
        """
        self._records.append(
            Provenance(
                key=key,
                value=None,
                internal_unit="1",
                dimension="choice",
                tier=TIER_CONFIG,
                tier_label=TIER_LABEL_CONFIG,
                source=source,
                confidence="high",
                note=note or choice,
            )
        )

    # ------------------------------------------------------------ 内部

    def _placeholder(self) -> tuple[float, int, str, str]:
        """占位值只用于结构演示：0、tier 0、低置信度。"""
        return (
            0.0,
            TIER_PLACEHOLDER,
            "占位（--allow-placeholder）",
            "low",
        )

    def _pick(
        self, request: ParameterRequest
    ) -> tuple[float, int, str, str]:
        if request.event is not None:
            return (
                to_internal(request.event, request.unit, request.dimension),
                TIER_EVENT,
                request.event_source or "事件级覆盖",
                "high",
            )

        if request.local is not None:
            return (
                to_internal(request.local, request.unit, request.dimension),
                TIER_LOCAL,
                request.local_source or "田块实测",
                "high",
            )

        if request.selected is not None:
            entry = self.library.get(request.selected)
            if entry is None:
                if self.allow_placeholder:
                    return self._placeholder()
                raise ParameterSourceError(
                    key=request.selected,
                    context=f"{request.context}（用户点名使用该库条目）",
                    doc_section=request.doc_section,
                    suggestions=(
                        f"在参数库中新增键 {request.selected!r} 并写明来源",
                        *request.suggestions,
                    ),
                    tried=("用户选择的库条目：未找到",),
                )
            return (
                to_internal(entry.value, entry.unit, request.dimension),
                TIER_SELECTED,
                entry.source,
                entry.confidence,
            )

        entry = self.library.get(request.key)
        if entry is not None:
            return (
                to_internal(entry.value, entry.unit, request.dimension),
                TIER_LITERATURE,
                entry.source,
                entry.confidence,
            )

        if self.allow_placeholder:
            return self._placeholder()

        raise ParameterSourceError(
            key=request.key,
            context=request.context,
            doc_section=request.doc_section,
            suggestions=request.suggestions,
            tried=(
                "事件级覆盖：未提供",
                "田块实测：未提供",
                "用户选择的库条目：未指定",
                "文献默认：库中无该键",
            ),
        )