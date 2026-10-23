"""模型状态。

状态量是「元素 × 土层」二维的储量 ``M``（mg·m⁻²）。
含量 ``C`` 是派生视图，不独立存储（SPEC Implementation Decisions）。

即使只有一层，``mass_mg_m2`` 也保持两层字典结构，不得压平成标量——
这是 T02 的验收标准之一，也是日后扩展多层的前提。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hmfms.config.field import SoilProfile
from hmfms.utils.units import mass_to_concentration, soil_mass_per_area

FluxDict = dict[str, dict[int, float]]
ConcentrationDict = dict[str, dict[int, float]]


@dataclass
class State:
    elements: tuple[str, ...]
    layer_ids: tuple[int, ...]
    soil_mass_kg_m2: dict[int, float]
    mass_mg_m2: dict[str, dict[int, float]] = field(default_factory=dict)

    @classmethod
    def from_profile(cls, elements: tuple[str, ...], profile: SoilProfile) -> State:
        soil_mass = {
            layer.layer_id: soil_mass_per_area(
                layer.bulk_density_kg_m3, layer.thickness_m
            )
            for layer in profile.layers
        }
        mass: dict[str, dict[int, float]] = {}
        for element in elements:
            mass[element] = {}
            for layer in profile.layers:
                concentration = profile.initial_mg_kg[element][layer.layer_id]
                mass[element][layer.layer_id] = (
                    concentration * soil_mass[layer.layer_id]
                )
        return cls(
            elements=elements,
            layer_ids=profile.layer_ids,
            soil_mass_kg_m2=soil_mass,
            mass_mg_m2=mass,
        )

    def snapshot(self) -> State:
        """返回状态副本，供当日所有通量模块读取（ADR-0007）。"""
        return State(
            elements=self.elements,
            layer_ids=self.layer_ids,
            soil_mass_kg_m2=dict(self.soil_mass_kg_m2),
            mass_mg_m2={e: dict(layers) for e, layers in self.mass_mg_m2.items()},
        )

    def concentration(self) -> ConcentrationDict:
        return {
            element: {
                layer_id: mass_to_concentration(
                    self.mass_mg_m2[element][layer_id],
                    self.soil_mass_kg_m2[layer_id],
                )
                for layer_id in self.layer_ids
            }
            for element in self.elements
        }

    def total_mass(self, element: str) -> float:
        return sum(self.mass_mg_m2[element].values())