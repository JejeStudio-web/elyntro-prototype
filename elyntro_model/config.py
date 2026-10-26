"""Configuration for the small, from-scratch Elyntro language model."""
from dataclasses import asdict, dataclass


@dataclass
class ModelConfig:
    vocab_size: int = 259
    block_size: int = 512
    n_layer: int = 6
    n_head: int = 6
    n_embd: int = 384
    dropout: float = 0.1
    bias: bool = True

    def __post_init__(self):
        if self.vocab_size < 259:
            raise ValueError("vocab_size must be at least 259 for ByteTokenizer")
        if self.block_size < 8:
            raise ValueError("block_size must be >= 8")
        if self.n_layer < 1 or self.n_head < 1 or self.n_embd < 1:
            raise ValueError("layer, head, and embedding sizes must be positive")
        if self.n_embd % self.n_head:
            raise ValueError("n_embd must be divisible by n_head")
        if not 0 <= self.dropout < 1:
            raise ValueError("dropout must be in [0, 1)")

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, values):
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{key: value for key, value in values.items() if key in allowed})
