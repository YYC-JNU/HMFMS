"""配置模型（pydantic v2）。

配置分三层，各管一件事：

* ``model.yaml`` —— 数据接线、时间范围、输出选项、闭合容差
* ``field.yaml`` —— 田块属性与土壤剖面引用
* ``soil.csv``  —— 逐层土壤属性与初始含量

本文件只做**结构与单位**校验；任何物理默认值都不出现在这里（ADR-0010）。
单位校验复用 ``hmfms.utils.units.ALLOWED_UNITS``，保证只有一处白名单。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hmfms.utils.units import to_internal

ONE_DAY = "1D"
OutputFrequency = Literal["daily", "monthly", "annual"]
OUTPUT_FREQUENCIES: tuple[OutputFrequency, ...] = ("daily", "monthly", "annual")


class _Strict(BaseModel):
    """禁止未知字段：拼错的配置键必须报错，而不是被静默忽略。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


class TimeConfig(_Strict):
    start: date
    end: date
    timestep: str = ONE_DAY

    @field_validator("timestep")
    @classmethod
    def _only_one_day(cls, value: str) -> str:
        if value.strip().upper() != ONE_DAY:
            raise ValueError(
                f"首版只支持 {ONE_DAY} 时间步长，收到 {value!r}。"
                "变步长需要保证累积输入质量一致，属于后续版本的工作。"
            )
        return ONE_DAY

    @model_validator(mode="after")
    def _ordered(self) -> TimeConfig:
        if self.end < self.start:
            raise ValueError(f"结束日期 {self.end} 早于开始日期 {self.start}")
        return self

    @property
    def n_days(self) -> int:
        """含首尾的天数。"""
        return (self.end - self.start).days + 1


class SoilUnitsConfig(_Strict):
    depth: str = "cm"
    bulk_density: str = "kg/m3"
    concentration: str = "mg/kg"

    @field_validator("depth")
    @classmethod
    def _check_depth(cls, value: str) -> str:
        to_internal(1.0, value, "thickness")
        return value

    @field_validator("bulk_density")
    @classmethod
    def _check_bulk_density(cls, value: str) -> str:
        to_internal(1.0, value, "bulk_density")
        return value

    @field_validator("concentration")
    @classmethod
    def _check_concentration(cls, value: str) -> str:
        to_internal(1.0, value, "concentration")
        return value


class SoilInputConfig(_Strict):
    file: Path
    units: SoilUnitsConfig = SoilUnitsConfig()


class MagnitudeLimit(_Strict):
    """量级检查区间，单位与内部通量一致（mg·m⁻²·d⁻¹）。

    阈值必须由配置给出，代码里不设默认——量级检查只用于提示，
    不能替代"文件内单位必须声明"这道校验。
    """

    min: float = Field(ge=0.0)
    max: float = Field(gt=0.0)

    @model_validator(mode="after")
    def _ordered(self) -> MagnitudeLimit:
        if self.max <= self.min:
            raise ValueError(f"max ({self.max}) 必须大于 min ({self.min})")
        return self


class NetCDFSourceConfig(_Strict):
    """NetCDF 沉降源的接线。

    维度名与变量名**全部来自配置**，适配器不假设 ``time`` / ``lat`` / ``lon``
    等任何具体命名（ADR 的 X7）。干/湿通量的变量名复用
    ``DepositionConfig.dry_columns`` / ``wet_columns`` 的“元素 → 变量名”映射。
    """

    cache: Path
    time_dim: str = Field(min_length=1)
    lat_dim: str = Field(min_length=1)
    lon_dim: str = Field(min_length=1)
    time_variable: str = Field(min_length=1)
    latitude_variable: str = Field(min_length=1)
    longitude_variable: str = Field(min_length=1)
    # 给出时覆盖时间变量的 units 属性；未给出则读文件属性。
    time_units: str | None = None


class DepositionConfig(_Strict):
    """沉降输入接线。

    干、湿沉降的列名按元素显式给出，不做字符串模板猜测——不同机构的
    NetCDF/CSV 命名差异太大，模板反而会制造静默错配。

    ``format`` 决定来源：``csv``（一等公民）或 ``netcdf``（适配器先提取到
    ``netcdf.cache``，主循环仍只读 CSV）。
    """

    file: Path
    format: Literal["csv", "netcdf"] = "csv"
    units: str
    date_column: str = "date"
    dry_columns: dict[str, str] = Field(min_length=1)
    wet_columns: dict[str, str] = Field(min_length=1)
    gap_policy: Literal["error", "interpolate"] = "error"
    magnitude_check: dict[str, MagnitudeLimit] | None = None
    netcdf: NetCDFSourceConfig | None = None

    @field_validator("units")
    @classmethod
    def _check_units(cls, value: str) -> str:
        to_internal(1.0, value, "flux")
        return value

    @model_validator(mode="after")
    def _netcdf_block_matches_format(self) -> DepositionConfig:
        if self.format == "netcdf" and self.netcdf is None:
            raise ValueError(
                "format: netcdf 时必须提供 inputs.deposition.netcdf 块，"
                "写明 cache、三个维度和三个坐标/时间变量名。"
            )
        if self.format == "csv" and self.netcdf is not None:
            raise ValueError(
                "inputs.deposition.netcdf 块只在 format: netcdf 时有效；"
                "CSV 路径请删除该块，避免两套接线同时存在。"
            )
        return self

    @model_validator(mode="after")
    def _same_elements(self) -> DepositionConfig:
        dry = set(self.dry_columns)
        wet = set(self.wet_columns)
        if dry != wet:
            raise ValueError(
                f"干沉降与湿沉降的元素集合不一致：仅干 {sorted(dry - wet)}，"
                f"仅湿 {sorted(wet - dry)}"
            )
        return self


class ParametersConfig(_Strict):
    """默认参数库位置。

    库文件缺失本身不报错——只有当某个参数真的需要回落到文献默认值时，
    才会因为"库中无该键"而报错，报错信息会指向这里。
    """

    library: Path


class InputsConfig(_Strict):
    soil: SoilInputConfig
    deposition: DepositionConfig | None = None


class OutputConfig(_Strict):
    directory: Path
    interval: tuple[OutputFrequency, ...] = ("daily",)

    @field_validator("interval")
    @classmethod
    def _known_intervals(
        cls, value: tuple[str, ...]
    ) -> tuple[OutputFrequency, ...]:
        allowed = set(OUTPUT_FREQUENCIES)
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ValueError(f"未知的输出频率 {unknown}；允许的值：{sorted(allowed)}")
        if not value:
            raise ValueError("输出频率不能为空")
        return cast(tuple[OutputFrequency, ...], tuple(dict.fromkeys(value)))


class FertilizerUnits(_Strict):
    amount: str = "kg/ha"
    concentration: str = "mg/kg"

    @field_validator("amount")
    @classmethod
    def _check_amount(cls, value: str) -> str:
        to_internal(1.0, value, "application_rate")
        return value

    @field_validator("concentration")
    @classmethod
    def _check_concentration(cls, value: str) -> str:
        to_internal(1.0, value, "concentration")
        return value


class FertilizerEventConfig(_Strict):
    """施肥事件表接线。

    表结构是固定契约：``date``、``fertilizer_id``、``amount`` 必需；
    每个元素可选的覆盖列名为 ``<element>_mg_kg_override``。
    """

    file: Path
    date_column: str = "date"
    units: FertilizerUnits = FertilizerUnits()


class HarvestUnits(_Strict):
    """产量单位与器官含量单位。

    字段名用 ``yield_rate`` 而不是 ``yield``——后者是 Python 关键字，
    在配置里硬要叫 ``yield`` 会让 schema 定义变丑，不值得。
    """

    yield_rate: str = "kg/ha"
    concentration: str = "mg/kg"

    @field_validator("yield_rate")
    @classmethod
    def _check_yield(cls, value: str) -> str:
        to_internal(1.0, value, "application_rate")
        return value

    @field_validator("concentration")
    @classmethod
    def _check_concentration(cls, value: str) -> str:
        to_internal(1.0, value, "concentration")
        return value


class HarvestEventConfig(_Strict):
    """收获事件表接线。

    表结构（固定契约）：``date``、``crop_id``、``organ``、``yield``、
    ``removal_fraction`` 必需；每个元素可选 ``<element>_mg_kg_override``。

    **一行一个器官**：同一次收获的籽粒与秸秆是两行，共用相同的
    ``(date, crop_id)``。器官名由数据给出，代码里不出现 ``grain`` / ``straw``。
    """

    file: Path
    date_column: str = "date"
    units: HarvestUnits = HarvestUnits()


class IrrigationUnits(_Strict):
    amount: str = "mm"
    concentration: str = "mg/L"

    @field_validator("amount")
    @classmethod
    def _check_amount(cls, value: str) -> str:
        to_internal(1.0, value, "water_depth")
        return value

    @field_validator("concentration")
    @classmethod
    def _check_concentration(cls, value: str) -> str:
        to_internal(1.0, value, "water_concentration")
        return value


class IrrigationEventConfig(_Strict):
    """灌溉事件表接线。

    表结构（固定契约）：``date``、``amount``、``water_source`` 必需；
    每个元素可选 ``<element>_mg_L_override``。

    **同日可以有多条**——一天灌两次是常态，不需要合并成一行。
    """

    file: Path
    date_column: str = "date"
    units: IrrigationUnits = IrrigationUnits()


class StrawUnits(_Strict):
    amount: str = "kg/ha"
    concentration: str = "mg/kg"

    @field_validator("amount")
    @classmethod
    def _check_amount(cls, value: str) -> str:
        to_internal(1.0, value, "application_rate")
        return value

    @field_validator("concentration")
    @classmethod
    def _check_concentration(cls, value: str) -> str:
        to_internal(1.0, value, "concentration")
        return value


class StrawEventConfig(_Strict):
    """秸秆事件表接线。

    表结构（固定契约）：``date``、``crop_id``、``organ``、``amount``、
    ``return_fraction`` 必需；每个元素可选 ``<element>_mg_kg_override``。

    一个事件同时产生两项通量：还田（输入）与外运（输出），两者的比例
    由 ``return_fraction`` 决定，且必须严格互补（`INV-2`）。

    **每个器官只能由一种机制处理**：要么出现在收获表（如籽粒），要么出现
    在秸秆表（如秸秆），不能两边都有——否则同一份质量会被扣两次。
    """

    file: Path
    date_column: str = "date"
    units: StrawUnits = StrawUnits()


class ManagementConfig(_Strict):
    fertilizer_events: FertilizerEventConfig | None = None
    irrigation_events: IrrigationEventConfig | None = None
    harvest_events: HarvestEventConfig | None = None
    straw_events: StrawEventConfig | None = None


class ModelConfig(_Strict):
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    time: TimeConfig
    elements: tuple[str, ...] = Field(min_length=1)
    element_configs: dict[str, Path] | None = None
    field: Path
    inputs: InputsConfig
    output: OutputConfig
    parameters: ParametersConfig | None = None
    management: ManagementConfig | None = None
    closure_tolerance: float = Field(default=1e-8, gt=0.0)

    @field_validator("elements")
    @classmethod
    def _unique_elements(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(e.strip() for e in value)
        if any(not e for e in cleaned):
            raise ValueError("元素列表里存在空字符串")
        duplicates = sorted({e for e in cleaned if cleaned.count(e) > 1})
        if duplicates:
            raise ValueError(f"元素列表中存在重复项：{duplicates}")
        return cleaned

    @model_validator(mode="after")
    def _element_configs_match_elements(self) -> ModelConfig:
        """元素配置只能挂在已声明的元素上，避免拼写错误静默失效。"""
        if not self.element_configs:
            return self
        unknown = sorted(set(self.element_configs) - set(self.elements))
        if unknown:
            raise ValueError(
                f"element_configs 含未在 elements 中声明的元素 {unknown}；"
                f"已声明：{list(self.elements)}"
            )
        return self

    @model_validator(mode="after")
    def _deposition_matches_elements(self) -> ModelConfig:
        deposition = self.inputs.deposition
        if deposition is None:
            return self
        expected = set(self.elements)
        for label, mapping in (
            ("dry_columns", deposition.dry_columns),
            ("wet_columns", deposition.wet_columns),
        ):
            actual = set(mapping)
            if actual != expected:
                raise ValueError(
                    f"inputs.deposition.{label} 的元素集合 {sorted(actual)} "
                    f"与顶层 elements {sorted(expected)} 不一致"
                )
        if deposition.magnitude_check is not None:
            unknown = sorted(set(deposition.magnitude_check) - expected)
            if unknown:
                raise ValueError(
                    f"inputs.deposition.magnitude_check 含未声明的元素 {unknown}"
                )
        return self