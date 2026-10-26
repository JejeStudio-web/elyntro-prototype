"""Train Elyntro's byte-level language model on authorized UTF-8 text files."""
import argparse
import json
import math
import random
from pathlib import Path
import torch
from .config import ModelConfig
from .model import ElyntroLanguageModel
from .tokenizer import ByteTokenizer


def read_corpus(path: Path) -> str:
    files = ([path] if path.is_file() and path.suffix.lower() in {".txt", ".md"} else
             sorted([*path.rglob("*.txt"), *path.rglob("*.md")]) if path.is_dir() else [])
    if not files:
        raise ValueError(f"No .txt or .md training files found at: {path}")
    parts = [p.read_text(encoding="utf-8", errors="replace").strip() for p in files]
    corpus = "\n\n".join(p for p in parts if p)
    if len(corpus.encode("utf-8")) < 1000:
        raise ValueError("Corpus is too small: provide at least 1000 UTF-8 bytes of authorized text")
    return corpus


def get_batch(data, batch_size, block_size, device):
    if data.numel() <= block_size + 1:
        raise ValueError("Not enough tokens for one training sequence")
    starts = torch.randint(0, data.numel() - block_size - 1, (batch_size,))
    x = torch.stack([data[i:i + block_size] for i in starts.tolist()])
    y = torch.stack([data[i + 1:i + block_size + 1] for i in starts.tolist()])
    return x.to(device), y.to(device)


def estimate_loss(model, train_data, val_data, batch_size, block_size, device, eval_iters=10):
    results, was_training = {}, model.training
    model.eval()
    with torch.no_grad():
        for name, split in (("train", train_data), ("val", val_data)):
            losses = []
            for _ in range(eval_iters):
                x, y = get_batch(split, batch_size, block_size, device)
                _, loss = model(x, y)
                losses.append(float(loss.item()))
            results[name] = sum(losses) / len(losses)
    model.train(was_training)
    return results


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", type=Path, required=True)
    p.add_argument("--out", type=Path, default=Path("checkpoints/elyntro-small.pt"))
    p.add_argument("--steps", type=int, default=5000)
    p.add_argument("--batch-size", type=int, default=16)
    p.add_argument("--block-size", type=int, default=256)
    p.add_argument("--layers", type=int, default=6)
    p.add_argument("--heads", type=int, default=6)
    p.add_argument("--embedding", type=int, default=384)
    p.add_argument("--dropout", type=float, default=0.1)
    p.add_argument("--learning-rate", type=float, default=3e-4)
    p.add_argument("--eval-interval", type=int, default=250)
    p.add_argument("--seed", type=int, default=1337)
    p.add_argument("--device", choices=("auto", "cpu", "cuda", "mps"), default="auto")
    a = p.parse_args()
    if min(a.steps, a.batch_size, a.eval_interval) < 1:
        p.error("steps, batch-size, and eval-interval must be positive")
    random.seed(a.seed)
    torch.manual_seed(a.seed)
    if a.device == "auto":
        device = "cuda" if torch.cuda.is_available() else ("mps" if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available() else "cpu")
    else:
        device = a.device
        if device == "cuda" and not torch.cuda.is_available(): p.error("CUDA is unavailable")
        if device == "mps" and not (getattr(torch.backends, "mps", None) and torch.backends.mps.is_available()): p.error("MPS is unavailable")
    corpus = read_corpus(a.data)
    tokenizer = ByteTokenizer()
    tokens = torch.tensor(tokenizer.encode(corpus, add_bos=True, add_eos=True), dtype=torch.long)
    split = int(len(tokens) * 0.95)
    train_data, val_data = tokens[:split], tokens[split:]
    if len(val_data) <= a.block_size + 1:
        p.error("Validation split is too small; reduce --block-size or add more text")
    cfg = ModelConfig(block_size=a.block_size, n_layer=a.layers, n_head=a.heads, n_embd=a.embedding, dropout=a.dropout)
    model = ElyntroLanguageModel(cfg).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=a.learning_rate, weight_decay=0.1)
    print(json.dumps({"device": device, "tokens": len(tokens), "parameters": model.parameter_count(), "config": cfg.to_dict()}))
    best_val = math.inf
    for step in range(1, a.steps + 1):
        warmup = min(1.0, step / max(1, min(100, a.steps // 10)))
        decay = 0.5 * (1.0 + math.cos(math.pi * step / a.steps))
        for group in optimizer.param_groups: group["lr"] = a.learning_rate * warmup * decay
        model.train()
        x, y = get_batch(train_data, a.batch_size, cfg.block_size, device)
        _, loss = model(x, y)
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()
        if step == 1 or step % a.eval_interval == 0 or step == a.steps:
            metrics = estimate_loss(model, train_data, val_data, a.batch_size, cfg.block_size, device)
            print(json.dumps({"step": step, **metrics, "lr": optimizer.param_groups[0]["lr"]}))
            if metrics["val"] < best_val:
                best_val = metrics["val"]
                a.out.parent.mkdir(parents=True, exist_ok=True)
                torch.save({"format": "elyntro-lm-v1", "config": cfg.to_dict(), "model_state_dict": model.state_dict(), "step": step, "best_val_loss": best_val, "tokenizer": "utf8-byte-v1", "training_seed": a.seed}, a.out)
                print(f"Saved best checkpoint: {a.out}")
    print("Training finished. Evaluate this experimental checkpoint before serving it to users.")


if __name__ == "__main__":
    main()
