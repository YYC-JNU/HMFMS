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


LEACHING_TOTAL = "total"
LEACHING_LABILE = "labile"
LEACHING_BASES: tuple[str, ...] = (LEACHING_TOTAL, LEACHING_LABILE)


class LeachingConfig(_Strict):
    """淋溶的开关与 Kd 口径（ADR-0011）。

    ``enabled`` 只决定"这个元素是否输出淋溶通量"；``kd_basis`` 决定
    Kd 的口径，**必须显式声明**，代码不做任何隐式换算：

    * ``total``  —— Kd = 全量含量 / 孔隙水浓度，``f_exch = 1``；
    * ``labile`` —— Kd = 可交换态含量 / 孔隙水浓度，需另给 ``f_exch``。

    数值（Kd 与 f_exch）一律来自参数库 ``kd.<元素>`` /
    ``f_exch.<元素>``，本文件只放结构，不放默认值（ADR-0010）。
    """

    enabled: bool = False
    kd_basis: str = LEACHING_TOTAL

    @field_validator("kd_basis")
    @classmethod
    def _known_basis(cls, value: str) -> str:
        cleaned = value.strip()
        if cleaned not in LEACHING_BASES:
            raise ValueError(
                f"未知的淋溶 Kd 口径 {value!r}；允许的值：{list(LEACHING_BASES)}"
            )
        return cleaned

    @model_validator(mode="after")
    def _basis_requires_enabled(self) -> LeachingConfig:
        if not self.enabled and self.kd_basis != LEACHING_TOTAL:
            raise ValueError(
                f"leaching.enabled 为 false 时不能设置 kd_basis={self.kd_basis!r}。"
                "要么把 enabled 改为 true，要么删除该键——"
                "配了不生效的口径声明会让溯源表产生误导。"
            )
        return self


class RunoffConfig(_Strict):
    """径流的开关与 Kd 口径（只能是溶解态，ADR-0012）。

    颗粒态统一记在 `erosion` 下；本块启用后输出式 20 的溶解态通量。
    """

    enabled: bool = False
    kd_basis: str = LEACHING_TOTAL

    @field_validator("kd_basis")
    @classmethod
    def _known_basis(cls, value: str) -> str:
        cleaned = value.strip()
        if cleaned not in LEACHING_BASES:
            raise ValueError(
                f"未知的径流 Kd 口径 {value!r}；允许的值：{list(LEACHING_BASES)}"
            )
        return cleaned

    @model_validator(mode="after")
    def _basis_requires_enabled(self) -> RunoffConfig:
        if not self.enabled and self.kd_basis != LEACHING_TOTAL:
            raise ValueError(
                f"runoff.enabled 为 false 时不能设置 kd_basis={self.kd_basis!r}。"
                "要么把 enabled 改为 true，要么删除该键。"
            )
        return self


class ErosionConfig(_Strict):
    """侵蚀开关（颗粒态，式 22）。速率与富集比都来自参数库。"""

    enabled: bool = False


class ElementConfig(_Strict):
    """单个元素的配置。``element`` 必须与 model.yaml 中的引用键一致。"""

    element: str = Field(min_length=1)
    crop_uptake: CropUptakeConfig = CropUptakeConfig()
    leaching: LeachingConfig = LeachingConfig()
    runoff: RunoffConfig = RunoffConfig()
    erosion: ErosionConfig = ErosionConfig()
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

    @property
    def leaching_enabled(self) -> bool:
        return self.leaching.enabled

    @property
    def uses_transport(self) -> bool:
        """是否需要解析 Kd / f_exch（淋溶或径流任一启用）。"""
        return self.leaching.enabled or self.runoff.enabled

    @property
    def kd_basis(self) -> str | None:
        """两个过程共用同一个 Kd 口径；都未启用时返回 None。"""
        if self.leaching.enabled and self.runoff.enabled:
            if self.leaching.kd_basis != self.runoff.kd_basis:
                raise ValueError(
                    f"元素 {self.element} 的 leaching.kd_basis="
                    f"{self.leaching.kd_basis!r} 与 runoff.kd_basis="
                    f"{self.runoff.kd_basis!r} 不一致。"
                    "Kd 是元素属性，淋溶与径流必须使用同一口径（ADR-0012）。"
                )
            return self.leaching.kd_basis
        if self.leaching.enabled:
            return self.leaching.kd_basis
        if self.runoff.enabled:
            return self.runoff.kd_basis
        return None