"""Download small, bounded samples from documented Hugging Face datasets.

Default sources: Wikimedia Spanish Wikipedia (CC BY-SA 3.0/GFDL) for language
modeling and OysterCoreAI Spanish SFT-General-Spanish-50K (CC BY 4.0) for
instruction tuning. Review each upstream dataset card and attribution terms
before use; public availability is not a blanket rights guarantee.
"""
import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

SOURCES = {
    "wikipedia-es": {
        "repo": "wikimedia/wikipedia", "config": "20231101.es", "split": "train",
        "license": "CC BY-SA 3.0 / GFDL (upstream dataset card)",
        "url": "https://huggingface.co/datasets/wikimedia/wikipedia", "kind": "text",
    },
    "spanish-sft": {
        "repo": "OysterCoreAI/SFT-General-Spanish-50K", "config": None, "split": "train",
        "license": "CC BY 4.0 (upstream dataset card)",
        "url": "https://huggingface.co/datasets/OysterCoreAI/SFT-General-Spanish-50K", "kind": "sft",
    },
}


def clean_text(value):
    if not isinstance(value, str):
        return ""
    value = value.replace("\x00", " ")
    value = re.sub(r"\s+", " ", value).strip()
    return value


def normalize_sft(row):
    """Normalize common chat/instruction schemas without assuming one column layout."""
    messages = row.get("messages") or row.get("conversations")
    if isinstance(messages, list):
        turns = []
        for item in messages:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", item.get("from", ""))).lower()
            content = item.get("content", item.get("value", item.get("text", "")))
            if role in {"human", "user", "instruction"}:
                role = "user"
            elif role in {"gpt", "assistant", "bot"}:
                role = "assistant"
            elif role in {"system"}:
                role = "system"
            else:
                continue
            content = clean_text(content)
            if content:
                turns.append({"role": role, "content": content})
        if any(m["role"] == "user" for m in turns) and any(m["role"] == "assistant" for m in turns):
            return {"messages": turns}
    instruction = clean_text(row.get("instruction") or row.get("prompt") or row.get("question") or row.get("user"))
    answer = clean_text(row.get("output") or row.get("response") or row.get("answer") or row.get("assistant"))
    context = clean_text(row.get("input") or row.get("context"))
    if instruction and answer:
        user_content = instruction + (("\n\nContexto:\n" + context) if context else "")
        return {"messages": [{"role": "user", "content": user_content}, {"role": "assistant", "content": answer}]}
    return None


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", choices=sorted(SOURCES), required=True)
    p.add_argument("--out-dir", type=Path, default=Path("data/downloaded"))
    p.add_argument("--max-records", type=int, default=10000)
    p.add_argument("--min-chars", type=int, default=80)
    p.add_argument("--seed", type=int, default=1337)
    a = p.parse_args()
    if a.max_records < 1 or a.min_chars < 1:
        p.error("max-records and min-chars must be positive")
    try:
        from datasets import load_dataset
    except ImportError as exc:
        raise SystemExit("Install requirements-model.txt (includes datasets) first") from exc
    source = SOURCES[a.source]
    kwargs = {"split": source["split"], "streaming": True}
    if source["config"]:
        ds = load_dataset(source["repo"], source["config"], **kwargs)
    else:
        ds = load_dataset(source["repo"], **kwargs)
    ds = ds.shuffle(seed=a.seed, buffer_size=min(max(1000, a.max_records * 2), 10000))
    a.out_dir.mkdir(parents=True, exist_ok=True)
    output = a.out_dir / (a.source + (".jsonl" if source["kind"] == "sft" else ".txt"))
    seen, written, digest = set(), 0, hashlib.sha256()
    with output.open("w", encoding="utf-8") as f:
        for row in ds:
            if source["kind"] == "text":
                title = clean_text(row.get("title", ""))
                text = clean_text(row.get("text", ""))
                value = (title + "\n\n" + text).strip() if title else text
                if len(value) < a.min_chars:
                    continue
                fingerprint = hashlib.sha256(value.casefold().encode("utf-8")).hexdigest()
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                f.write(value + "\n\n")
                digest.update(value.encode("utf-8"))
            else:
                normalized = normalize_sft(row)
                if not normalized:
                    continue
                value = json.dumps(normalized, ensure_ascii=False)
                fingerprint = hashlib.sha256(value.casefold().encode("utf-8")).hexdigest()
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                f.write(value + "\n")
                digest.update(value.encode("utf-8"))
            written += 1
            if written >= a.max_records:
                break
    if written == 0:
        output.unlink(missing_ok=True)
        raise SystemExit("No compatible records found. Inspect upstream schema; no empty dataset retained.")
    manifest = {
        "source_id": a.source, "dataset": source["repo"], "config": source["config"],
        "split": source["split"], "upstream_url": source["url"], "declared_license": source["license"],
        "records_written": written, "output_file": output.name, "output_sha256_content": digest.hexdigest(),
        "created_at_utc": datetime.now(timezone.utc).isoformat(), "seed": a.seed,
        "limitations": ["Streaming sample is capped, not a full corpus", "Upstream license and provenance must be reviewed before redistribution or commercial use"],
    }
    (a.out_dir / (a.source + ".manifest.json")).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), **manifest}, ensure_ascii=False))


if __name__ == "__main__":
    main()
