"""田块配置与土壤剖面的数据模型。"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Location(_Strict):
    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)


class FieldConfig(_Strict):
    """田块属性。数据接线（哪份文件喂哪个过程）在 model.yaml，这里只管田块本身。"""

    field_id: str = Field(min_length=1)
    area_ha: float = Field(gt=0.0)
    location: Location
    notes: str | None = None


@dataclass(frozen=True)
class SoilLayer:
    """一层土壤。深度已换算为 m，容重为 kg·m⁻³，含量为 mg·kg⁻¹。"""

    layer_id: int
    top_m: float
    bottom_m: float
    bulk_density_kg_m3: float
    ph: float
    organic_matter: float
    clay_fraction: float
    cec: float

    @property
    def thickness_m(self) -> float:
        return self.bottom_m - self.top_m


@dataclass(frozen=True)
class SoilProfile:
    """整份剖面。

    ``initial_mg_kg`` 始终保持「元素 → 土层 → 含量」两层结构；
    即使只有一层，也不得压平成 ``元素 → 含量``（T02 验收标准）。
    """

    layers: tuple[SoilLayer, ...]
    initial_mg_kg: dict[str, dict[int, float]]

    @property
    def layer_ids(self) -> tuple[int, ...]:
        return tuple(layer.layer_id for layer in self.layers)

    def layer(self, layer_id: int) -> SoilLayer:
        for layer in self.layers:
            if layer.layer_id == layer_id:
                return layer
        raise KeyError(f"剖面中不存在土层 {layer_id}")