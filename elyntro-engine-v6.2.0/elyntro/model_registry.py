"""Model capability registry for Elyntro's provider-independent router."""
from __future__ import annotations
from dataclasses import dataclass, field

@dataclass(frozen=True)
class ModelProfile:
    model_id: str
    provider: str
    capabilities: frozenset[str]
    input_cost_per_million: float
    output_cost_per_million: float
    max_input_tokens: int
    max_output_tokens: int
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.model_id.strip() or not self.provider.strip():
            raise ValueError("model_id and provider are required")
        if self.input_cost_per_million < 0 or self.output_cost_per_million < 0:
            raise ValueError("Costs cannot be negative")
        if self.max_input_tokens < 1 or self.max_output_tokens < 1:
            raise ValueError("Token limits must be positive")
        if not self.capabilities:
            raise ValueError("At least one capability is required")

@dataclass
class ModelRegistry:
    _models: dict[str, ModelProfile] = field(default_factory=dict)

    def register(self, model: ModelProfile, *, replace: bool = False) -> None:
        if model.model_id in self._models and not replace:
            raise ValueError(f"Model already registered: {model.model_id}")
        self._models[model.model_id] = model

    def get(self, model_id: str) -> ModelProfile:
        try:
            return self._models[model_id]
        except KeyError:
            raise KeyError(f"Unknown model: {model_id}") from None

    def list_enabled(self) -> list[ModelProfile]:
        return [m for m in self._models.values() if m.enabled]
