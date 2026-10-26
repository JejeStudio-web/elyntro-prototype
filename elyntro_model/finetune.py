"""Supervised fine-tuning of a trusted Elyntro base checkpoint on JSONL chat data."""
import argparse
import json
import random
from pathlib import Path
import torch
from .config import ModelConfig
from .model import ElyntroLanguageModel
from .tokenizer import ByteTokenizer


def render(messages):
    chunks = []
    for message in messages:
        role, content = message.get("role"), message.get("content")
        if role not in {"system", "user", "assistant"} or not isinstance(content, str) or not content.strip():
            continue
        chunks.append(f"<|{role}|>\\n{content.strip()}\\n")
    return "".join(chunks)


def load_examples(path, tokenizer, block_size):
    examples = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError as exc:
                raise ValueError(f"Invalid JSON at {path}:{line_number}") from exc
            messages = row.get("messages")
            if not isinstance(messages, list):
                continue
            ids, answer_mask = [], []
            for message in messages:
                role, content = message.get("role"), message.get("content")
                if role not in {"system", "user", "assistant"} or not isinstance(content, str):
                    continue
                segment = f"<|{role}|>\\n{content.strip()}\\n"
                token_ids = tokenizer.encode(segment)
                ids.extend(token_ids)
                answer_mask.extend([role == "assistant"] * len(token_ids))
            ids = [tokenizer.BOS] + ids[:block_size - 1]
            answer_mask = [False] + answer_mask[:block_size - 1]
            if len(ids) < 8 or not any(answer_mask):
                continue
            ids = ids + [tokenizer.PAD] * (block_size - len(ids))
            targets = ids[1:] + [tokenizer.PAD]
            # Predict assistant tokens only; ignore prompt and padding.
            for index in range(len(targets)):
                if index + 1 >= len(answer_mask) or not answer_mask[index + 1] or ids[index + 1] == tokenizer.PAD:
                    targets[index] = tokenizer.PAD
            examples.append((ids, targets))
    if len(examples) < 2:
        raise ValueError("Need at least two valid instruction examples with assistant answers")
    return examples


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--checkpoint", type=Path, required=True)
    p.add_argument("--data", type=Path, required=True, help="JSONL records with a messages array")
    p.add_argument("--out", type=Path, default=Path("checkpoints/elyntro-instruct.pt"))
    p.add_argument("--steps", type=int, default=1000)
    p.add_argument("--batch-size", type=int, default=4)
    p.add_argument("--learning-rate", type=float, default=1e-5)
    p.add_argument("--seed", type=int, default=2026)
    p.add_argument("--device", choices=("auto", "cpu", "cuda", "mps"), default="auto")
    a = p.parse_args()
    if min(a.steps, a.batch_size) < 1:
        p.error("steps and batch-size must be positive")
    random.seed(a.seed)
    torch.manual_seed(a.seed)
    if a.device == "auto":
        device = "cuda" if torch.cuda.is_available() else ("mps" if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available() else "cpu")
    else:
        device = a.device
    checkpoint = torch.load(a.checkpoint, map_location=device, weights_only=True)
    if checkpoint.get("format") != "elyntro-lm-v1" or checkpoint.get("tokenizer") != "utf8-byte-v1":
        raise ValueError("Unsupported base checkpoint")
    config = ModelConfig.from_dict(checkpoint["config"])
    tokenizer = ByteTokenizer()
    examples = load_examples(a.data, tokenizer, config.block_size)
    random.shuffle(examples)
    model = ElyntroLanguageModel(config).to(device)
    model.load_state_dict(checkpoint["model_state_dict"])
    optimizer = torch.optim.AdamW(model.parameters(), lr=a.learning_rate, weight_decay=0.01)
    model.train()
    for step in range(1, a.steps + 1):
        batch = random.choices(examples, k=min(a.batch_size, len(examples)))
        x = torch.tensor([e[0] for e in batch], dtype=torch.long, device=device)
        y = torch.tensor([e[1] for e in batch], dtype=torch.long, device=device)
        _, loss = model(x, y)
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()
        if step == 1 or step % 50 == 0 or step == a.steps:
            print(json.dumps({"step": step, "loss": float(loss.item()), "examples": len(examples)}))
    a.out.parent.mkdir(parents=True, exist_ok=True)
    torch.save({"format": "elyntro-lm-v1", "config": config.to_dict(), "model_state_dict": model.state_dict(),
                "step": a.steps, "tokenizer": "utf8-byte-v1", "training_stage": "supervised-instruction-finetuning",
                "base_checkpoint": str(a.checkpoint), "seed": a.seed}, a.out)
    print(json.dumps({"saved": str(a.out), "parameters": model.parameter_count(), "warning": "Evaluate before deployment; training loss is not a quality benchmark."}))


if __name__ == "__main__":
    main()
