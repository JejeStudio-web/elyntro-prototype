# Elyntro Base Language Model (experimental v0.1)

This package is Elyntro's first from-scratch, trainable language-model implementation. It is an engineering baseline, not a pretrained assistant comparable to ChatGPT. Useful language capability only emerges after training on a large, clean, legally usable corpus and evaluation. No trained weights or corpus are included.

## Architecture
- Decoder-only GPT-style transformer with causal self-attention and pre-normalized residual blocks.
- Learned token and position embeddings; token embedding weights tied to the output projection.
- GELU feed-forward network with 4x expansion, dropout, and final layer normalization.
- Next-token cross-entropy objective; AdamW, gradient clipping, warmup, cosine learning-rate decay, and train/validation loss tracking.
- UTF-8 byte tokenizer with 256 byte values plus PAD/BOS/EOS IDs (259 total). Deterministic and auditable, but less efficient than a production subword tokenizer.
- CPU, CUDA, and Apple Silicon MPS selection when supported by the installed PyTorch build.

## Files
- elyntro_model/config.py: model hyperparameters.
- elyntro_model/tokenizer.py: byte tokenizer.
- elyntro_model/model.py: transformer, loss, and autoregressive generation.
- elyntro_model/train.py: corpus ingestion, training, validation, and best-checkpoint saving.
- elyntro_model/generate.py: local checkpoint inference CLI.
- tests/test_elyntro_language_model.py: model-level regression tests.

## Train locally
Install a PyTorch build appropriate for your hardware:

    python -m pip install -r requirements-model.txt

Prepare a UTF-8 .txt or .md corpus that you are legally entitled to use. Then run:

    python -m elyntro_model.train --data ./data/corpus.txt --out ./checkpoints/elyntro-small.pt --steps 5000 --batch-size 16 --block-size 256 --layers 6 --heads 6 --embedding 384

Generate text from the resulting checkpoint:

    python -m elyntro_model.generate --checkpoint ./checkpoints/elyntro-small.pt --prompt "Elyntro es un asistente que" --tokens 120

For a smoke test use a small authorized corpus and reduce the block size, layers, heads, embedding dimension, and number of steps. The default configuration is not guaranteed to fit every device. Training speed and quality depend on hardware, corpus size, sequence length, and data quality.

## Implemented versus not yet complete

Implemented: model architecture, deterministic tokenizer, training loop, validation metrics, checkpoint format, inference CLI, and regression tests.

Not yet implemented or proven: pretrained weights, a curated/licensed Spanish and multilingual corpus, instruction fine-tuning, preference optimization, retrieval augmentation, factuality/safety evaluations, benchmark suite, quantization, distributed training, model registry/versioning, production inference server, integration of native checkpoints into the API, multimodal encoders/decoders, or verified quality against existing models.

The existing Elyntro API adapter can call an OpenAI-compatible inference server (including Ollama), but this is a separate deployment step. This package does not automatically host a model on Render. A free web service should not be assumed to provide GPU capacity. Do not expose an unauthenticated inference server publicly. Load only checkpoint files produced by trusted training runs.

## Acceptance gates before calling it a usable assistant
1. Install dependencies and run pytest, including the model tests.
2. Run a short training job and verify finite train/validation loss and checkpoint reload.
3. Evaluate held-out Spanish, English, reasoning, instruction-following, hallucination, and safety cases; publish methodology and results.
4. Add supervised instruction fine-tuning on authorized examples, followed by red-team and regression evaluation.
5. Serve behind authentication, bounded context/output, request and compute quotas, privacy-aware logging, and explicit health checks.
6. Compare quality, latency, memory, and cost against candidate open-weight models before deciding whether this base model is viable.