"""元素配置（``elements/<E>.yaml``）。

每个元素可以单独选择作物吸收模式（SPEC 用户故事 2）：

* ``concentration`` —— 器官含量取自参数库或事件覆盖，与土壤含量无关；
* ``bcf`` —— 器官含量 = BCF × 当日土壤总量含量（式 15，状态反馈）。

**一个元素只能取一种模式。** 同时写两种不是"更保险"，而是两张互斥的
假设叠在一起；装载期直接报错，不做隐式取舍。

本文件只描述**结构与枚举**，不含任何物理默认值（ADR-0010）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

UPTAKE_CONCENTRATION = "concentration"
UPTAKE_BCF = "bcf"
UPTAKE_MODES: tuple[str, ...] = (UPTAKE_CONCENTRATION, UPTAKE_BCF)

BASIS_TOTAL = "total"
BASIS_AVAILABLE = "available"
UPTAKE_BASES: tuple[str, ...] = (BASIS_TOTAL, BASIS_AVAILABLE)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CropUptakeConfig(_Strict):
    """作物吸收子模型的模式与分母口径。

    ``mode`` 允许写成字符串或列表：列表是**显式表达冲突**的写法，
    校验时会被拒绝并点名元素，而不是"后者覆盖前者"。
    """

    mode: tuple[str, ...] = (UPTAKE_CONCENTRATION,)
    basis: str = BASIS_TOTAL

    @field_validator("mode", mode="before")
    @classmethod
    def _normalize_mode(cls, value: object) -> object:
        if isinstance(value, str):
            return (value.strip(),)
        if isinstance(value, (list, tuple)):
            return tuple(str(item).strip() for item in value)
        return value

    @field_validator("mode")
    @classmethod
    def _known_modes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("crop_uptake.mode 不能为空")
        unknown = sorted(set(value) - set(UPTAKE_MODES))
        if unknown:
            raise ValueError(
                f"未知的作物吸收模式 {unknown}；允许的值：{list(UPTAKE_MODES)}"
            )
        return value

    @field_validator("basis")
    @classmethod
    def _known_basis(cls, value: str) -> str:
        cleaned = value.strip()
        if cleaned not in UPTAKE_BASES:
            raise ValueError(
                f"未知的 BCF 分母口径 {value!r}；允许的值：{list(UPTAKE_BASES)}"
            )
        return cleaned


class DiagnosticsConfig(_Strict):
    """关闭过程时的按元素提示强度（HARDCODE_AUDIT R1）。

    不在代码里写 ``if element == "Pb"``；强度与文案都放在元素配置中，
    默认是一般警告。
    """

    runoff_disabled_severity: Literal["normal", "strong"] = "normal"
    runoff_disabled_note: str = ""


class ElementConfig(_Strict):
    """单个元素的配置。``element`` 必须与 model.yaml 中的引用键一致。"""

    element: str = Field(min_length=1)
    crop_uptake: CropUptakeConfig = CropUptakeConfig()
    diagnostics: DiagnosticsConfig = DiagnosticsConfig()

    @model_validator(mode="after")
    def _exactly_one_mode(self) -> ElementConfig:
        modes = self.crop_uptake.mode
        if len(set(modes)) > 1:
            raise ValueError(
                f"元素 {self.element} 同时配置了多种作物吸收模式 "
                f"{sorted(set(modes))}；同一元素只能取一种——"
                "浓度模式与 BCF 模式互斥，请删除其中一个。"
            )
        if self.uptake_mode == UPTAKE_CONCENTRATION and (
            self.crop_uptake.basis != BASIS_TOTAL
        ):
            raise ValueError(
                f"元素 {self.element} 使用 concentration 模式时不能设置 "
                f"basis={self.crop_uptake.basis!r}；分母口径只对 bcf 模式有效。"
            )
        if self.uptake_mode == UPTAKE_BCF and (
            self.crop_uptake.basis == BASIS_AVAILABLE
        ):
            raise ValueError(
                f"元素 {self.element} 的 basis: available（以有效态为分母）"
                "在 v1 未实现：有效态只作诊断，不回馈主状态。"
                "见 docs/equations.md §4.2 与 SPEC 的 Out of Scope。"
            )
        return self

    @property
    def uptake_mode(self) -> str:
        return self.crop_uptake.mode[0]

    @property
    def uses_state(self) -> bool:
        return self.uptake_mode == UPTAKE_BCF