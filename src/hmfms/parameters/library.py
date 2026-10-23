"""默认参数库。

一个参数库是一组带出处的条目。两条硬规则（ADR-0010）：

* ``source`` 为空的条目**视为无效**，等同于参数缺失——不允许"有值但不知
  道从哪来"。
* 库只提供"文献默认"与"用户可选条目"，永远不覆盖事件级与实测值。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

Confidence = Literal["low", "medium", "high"]


class _EntryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1)
    value: float
    unit: str = Field(min_length=1)
    source: str = ""
    confidence: Confidence = "low"
    applicability: str | None = None


class _LibraryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parameters: list[_EntryModel] = Field(default_factory=list)


@dataclass(frozen=True)
class ParameterEntry:
    key: str
    value: float
    unit: str
    source: str
    confidence: str
    applicability: str | None


def load_library(path: Path) -> dict[str, ParameterEntry]:
    if not path.is_file():
        raise FileNotFoundError(f"参数库不存在：{path}")
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    if payload is None:
        return {}

    parsed = _LibraryModel.model_validate(payload)
    entries: dict[str, ParameterEntry] = {}
    for model in parsed.parameters:
        source = model.source.strip()
        if not source:
            logger.warning(
                "参数库条目 %r 的来源为空，视为无效并忽略（%s）。"
                "每个默认值都必须能追溯到文献、标准或数据库。",
                model.key,
                path,
            )
            continue
        if model.key in entries:
            raise ValueError(f"参数库 {path} 中键 {model.key!r} 重复")
        entries[model.key] = ParameterEntry(
            key=model.key,
            value=model.value,
            unit=model.unit,
            source=source,
            confidence=model.confidence,
            applicability=model.applicability,
        )
    return entries