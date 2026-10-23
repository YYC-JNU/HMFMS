"""观测对比配置（``validation.yaml``）。

它与主模型配置**完全分离**（ADR-0006）：主模型不认识这个文件，也不读观测数据。
观测列名、匹配规则与输出文件名全部在这里声明，代码里没有默认列名。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hmfms.utils.units import to_internal

MatchRule = Literal["exact", "nearest", "linear"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SimulationSeriesConfig(_Strict):
    """已有模拟输出的接线。数值列由元素与土层从输出契约推导。"""

    file: Path
    date_column: str = Field(default="date", min_length=1)


class ObservationSeriesConfig(_Strict):
    """用户观测表的接线。列名必须显式给出，不做模板猜测。"""

    file: Path
    date_column: str = Field(min_length=1)
    value_column: str = Field(min_length=1)
    units: str = "mg/kg"

    @field_validator("units")
    @classmethod
    def _check_units(cls, value: str) -> str:
        to_internal(1.0, value, "concentration")
        return value


class MatchConfig(_Strict):
    """日期匹配规则。``tolerance_days`` 没有默认值，必须由用户声明。"""

    rule: MatchRule = "exact"
    tolerance_days: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _tolerance_matches_rule(self) -> MatchConfig:
        if self.rule != "exact" and self.tolerance_days <= 0:
            raise ValueError(
                f"match.rule: {self.rule} 时必须给出正的 match.tolerance_days；"
                "容差是科学选择，代码不提供默认值。"
            )
        return self


class MetricSettings(_Strict):
    """指标计算的最小要求。默认值只表示“统计上最低可算”，不是科学推荐。"""

    trend_min_points: int = Field(default=3, ge=2)


class ValidationOutputConfig(_Strict):
    directory: Path
    report_name: str = "report.md"
    figure_name: str = "comparison.png"
    points_name: str = "matched_points.csv"

    @field_validator("report_name")
    @classmethod
    def _report_suffix(cls, value: str) -> str:
        if not value.lower().endswith(".md"):
            raise ValueError("output.report_name 必须以 .md 结尾")
        return value

    @field_validator("figure_name")
    @classmethod
    def _figure_suffix(cls, value: str) -> str:
        if not value.lower().endswith(".png"):
            raise ValueError("output.figure_name 必须以 .png 结尾")
        return value

    @field_validator("points_name")
    @classmethod
    def _points_suffix(cls, value: str) -> str:
        if not value.lower().endswith(".csv"):
            raise ValueError("output.points_name 必须以 .csv 结尾")
        return value

    @model_validator(mode="after")
    def _distinct_names(self) -> ValidationOutputConfig:
        names = {self.report_name, self.figure_name, self.points_name}
        if len(names) != 3:
            raise ValueError("report_name / figure_name / points_name 不能重名")
        return self


class ValidationConfig(_Strict):
    element: str = Field(min_length=1)
    layer_id: int = Field(ge=1)
    simulation: SimulationSeriesConfig
    observations: ObservationSeriesConfig
    match: MatchConfig = MatchConfig()
    metrics: MetricSettings = MetricSettings()
    output: ValidationOutputConfig

    @property
    def simulation_value_column(self) -> str:
        """模拟输出的数值列名由输出契约唯一决定。"""
        return f"soil_concentration_{self.element}_L{self.layer_id}_mg_kg"