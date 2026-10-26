"""A compact GPT-style decoder-only transformer implemented in PyTorch."""
import torch
from torch import nn
from torch.nn import functional as F

from .config import ModelConfig


class CausalSelfAttention(nn.Module):
    def __init__(self, config: ModelConfig):
        super().__init__()
        self.n_head = config.n_head
        self.n_embd = config.n_embd
        self.qkv = nn.Linear(config.n_embd, 3 * config.n_embd, bias=config.bias)
        self.proj = nn.Linear(config.n_embd, config.n_embd, bias=config.bias)
        self.attn_dropout = nn.Dropout(config.dropout)
        self.resid_dropout = nn.Dropout(config.dropout)

    def forward(self, x):
        batch, length, channels = x.shape
        head_dim = channels // self.n_head
        q, k, v = self.qkv(x).split(channels, dim=2)
        q = q.view(batch, length, self.n_head, head_dim).transpose(1, 2)
        k = k.view(batch, length, self.n_head, head_dim).transpose(1, 2)
        v = v.view(batch, length, self.n_head, head_dim).transpose(1, 2)
        # PyTorch's scaled_dot_product_attention applies a causal mask without
        # materializing a full attention matrix when the backend supports it.
        y = F.scaled_dot_product_attention(
            q, k, v, attn_mask=None, dropout_p=self.attn_dropout.p if self.training else 0.0,
            is_causal=True,
        )
        y = y.transpose(1, 2).contiguous().view(batch, length, channels)
        return self.resid_dropout(self.proj(y))


class MLP(nn.Module):
    def __init__(self, config: ModelConfig):
        super().__init__()
        self.layers = nn.Sequential(
            nn.Linear(config.n_embd, 4 * config.n_embd, bias=config.bias),
            nn.GELU(),
            nn.Linear(4 * config.n_embd, config.n_embd, bias=config.bias),
            nn.Dropout(config.dropout),
        )

    def forward(self, x):
        return self.layers(x)


class TransformerBlock(nn.Module):
    def __init__(self, config: ModelConfig):
        super().__init__()
        self.ln_1 = nn.LayerNorm(config.n_embd, bias=config.bias)
        self.attn = CausalSelfAttention(config)
        self.ln_2 = nn.LayerNorm(config.n_embd, bias=config.bias)
        self.mlp = MLP(config)

    def forward(self, x):
        x = x + self.attn(self.ln_1(x))
        return x + self.mlp(self.ln_2(x))


class ElyntroLanguageModel(nn.Module):
    """Small GPT-like next-token model. It starts untrained until trained on a corpus."""
    def __init__(self, config: ModelConfig | None = None):
        super().__init__()
        self.config = config or ModelConfig()
        cfg = self.config
        self.token_embedding = nn.Embedding(cfg.vocab_size, cfg.n_embd)
        self.position_embedding = nn.Embedding(cfg.block_size, cfg.n_embd)
        self.drop = nn.Dropout(cfg.dropout)
        self.blocks = nn.ModuleList([TransformerBlock(cfg) for _ in range(cfg.n_layer)])
        self.final_norm = nn.LayerNorm(cfg.n_embd, bias=cfg.bias)
        self.lm_head = nn.Linear(cfg.n_embd, cfg.vocab_size, bias=False)
        self.lm_head.weight = self.token_embedding.weight
        self.apply(self._init_weights)
        for name, parameter in self.named_parameters():
            if name.endswith("proj.weight"):
                nn.init.normal_(parameter, mean=0.0, std=0.02 / (2 * cfg.n_layer) ** 0.5)

    @staticmethod
    def _init_weights(module):
        if isinstance(module, nn.Linear):
            nn.init.normal_(module.weight, mean=0.0, std=0.02)
            if module.bias is not None:
                nn.init.zeros_(module.bias)
        elif isinstance(module, nn.Embedding):
            nn.init.normal_(module.weight, mean=0.0, std=0.02)

    def forward(self, idx, targets=None):
        if idx.ndim != 2:
            raise ValueError("idx must have shape [batch, sequence]")
        batch, length = idx.shape
        if length > self.config.block_size:
            raise ValueError(f"sequence length {length} exceeds block_size {self.config.block_size}")
        positions = torch.arange(length, device=idx.device)
        x = self.drop(self.token_embedding(idx) + self.position_embedding(positions)[None, :, :])
        for block in self.blocks:
            x = block(x)
        logits = self.lm_head(self.final_norm(x))
        loss = None
        if targets is not None:
            if targets.shape != idx.shape:
                raise ValueError("targets must have the same shape as idx")
            loss = F.cross_entropy(logits.reshape(-1, logits.size(-1)), targets.reshape(-1), ignore_index=256)
        return logits, loss

    @torch.no_grad()
    def generate(self, idx, max_new_tokens=200, temperature=0.8, top_k=40, eos_token_id=258):
        if temperature <= 0:
            raise ValueError("temperature must be > 0")
        if max_new_tokens < 0:
            raise ValueError("max_new_tokens must be >= 0")
        was_training = self.training
        self.eval()
        try:
            for _ in range(max_new_tokens):
                context = idx[:, -self.config.block_size:]
                logits, _ = self(context)
                logits = logits[:, -1, :] / temperature
                if top_k is not None and 0 < top_k < logits.size(-1):
                    values, _ = torch.topk(logits, top_k)
                    logits[logits < values[:, [-1]]] = -float("inf")
                probs = F.softmax(logits, dim=-1)
                next_token = torch.multinomial(probs, num_samples=1)
                idx = torch.cat((idx, next_token), dim=1)
                if eos_token_id is not None and torch.all(next_token == eos_token_id):
                    break
            return idx
        finally:
            self.train(was_training)

    def parameter_count(self, trainable_only=True):
        return sum(p.numel() for p in self.parameters() if not trainable_only or p.requires_grad)
