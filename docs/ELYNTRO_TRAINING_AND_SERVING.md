# Elyntro model pipeline: data, training, evaluation, serving

This adds a reproducible pipeline around the experimental base Transformer. It does not ship trained weights or claim assistant-level quality. Training must be run on an actual machine with available disk, RAM, and compute, and results must be measured.

## 1. Data acquisition and provenance

Install the model dependencies with a PyTorch build appropriate for your hardware:

    python -m pip install -r requirements-model.txt

Download a bounded sample of Spanish Wikipedia text for base-language modeling:

    python -m elyntro_model.data --source wikipedia-es --max-records 10000 --out-dir data/downloaded

The upstream Wikimedia Wikipedia card lists the Spanish 20231101 subset under CC BY-SA 3.0 and GFDL. Preserve attribution and review share-alike/license obligations before distributing weights or outputs. Source: https://huggingface.co/datasets/wikimedia/wikipedia

Download a bounded sample of Spanish synthetic instruction data:

    python -m elyntro_model.data --source spanish-sft --max-records 5000 --out-dir data/downloaded

The OysterCoreAI/SFT-General-Spanish-50K dataset card declares CC BY 4.0. Review the current card, attribution requirements, provenance, and suitability before use. Source: https://huggingface.co/datasets/OysterCoreAI/SFT-General-Spanish-50K

The script streams rows, caps records, removes exact normalized duplicates, writes a manifest with source, declared license, count, timestamp, and content hash, and never commits corpus data to Git. A small sample validates the pipeline but is not enough for competitive pretraining. Public availability alone does not guarantee that every item is suitable for every use.

## 2. Base pretraining

    python -m elyntro_model.train --data data/downloaded/wikipedia-es.txt --out checkpoints/elyntro-es-base.pt --steps 5000 --batch-size 4 --block-size 128 --layers 4 --heads 4 --embedding 128 --device auto

This small configuration is intended for an initial experiment. Increase data, context length, model size, and compute only after measuring memory use. UTF-8 byte tokenization is inefficient; a capable assistant will require much more data and a better tokenizer/model recipe.

## 3. Supervised instruction tuning

    python -m elyntro_model.finetune --checkpoint checkpoints/elyntro-es-base.pt --data data/downloaded/spanish-sft.jsonl --out checkpoints/elyntro-es-instruct.pt --steps 1000 --batch-size 2 --device auto

The fine-tuner trains assistant-response tokens while masking user/system prompt tokens. It uses a simple textual chat template, not a standardized chat protocol. Keep a separate held-out evaluation set and do not train on test prompts.

## 4. Evaluation

Create a UTF-8 JSONL file with one object per case, for example:

    {"prompt":"Explica la fotosíntesis en dos frases.","reference":"...","category":"spanish_knowledge"}
    {"prompt":"Si no tienes datos suficientes, ¿qué debes hacer?","reference":"...","category":"calibration"}

Then run:

    python -m elyntro_model.evaluate --checkpoint checkpoints/elyntro-es-instruct.pt --data data/eval.jsonl --out reports/evaluation.json --max-cases 100

The report includes generations, latency, and optional exact-match rate. Exact match is not a semantic score. Add independently authored tests for factuality, math, coding, instruction following, privacy, prompt injection, and safety; humans should review representative outputs before deployment.

## 5. Authenticated native inference server

Set a long random secret in ELYNTRO_INFERENCE_API_KEY and the checkpoint path in ELYNTRO_MODEL_CHECKPOINT, then run:

    python -m elyntro_model.server

The server binds to 127.0.0.1 by default, exposes /health, /v1/models, and /v1/chat/completions, requires a bearer key for inference routes, bounds request size/messages/output tokens, and applies an in-process per-IP rate limit. It is not a hardened multi-tenant service; use TLS, a reverse proxy/firewall, persistent shared rate limits, monitoring, and secret management before public exposure. Streaming, tools, multimodal input, cancellation, and continuous batching are not supported.

## 6. Connect to the Elyntro API

Run the native server on a machine reachable by the Elyntro API service. Configure the API service with:

- ELYNTRO_MODEL_PROVIDER=openai-compatible
- ELYNTRO_MODEL_BASE_URL=http://<private-host>:8090/v1
- ELYNTRO_MODEL=elyntro-native
- ELYNTRO_MODEL_API_KEY equal to the native server key

Use private networking or a secure TLS endpoint; do not expose an unauthenticated model server publicly. The existing API adapter supports OpenAI-compatible endpoints. The native server is separate from the Flask web app so PyTorch is not installed in the lightweight web-service environment.

## Compute and readiness

The repository and CI runner do not automatically provide a GPU. The small model can be smoke-tested on CPU, but training time, memory, and quality depend on actual hardware and data. This change does not reserve cloud GPUs, spend money, upload weights to a model hub, or deploy the inference server. A checkpoint is not ready until training completes, it reloads, evaluation results are reviewed, and authenticated inference smoke tests pass.
