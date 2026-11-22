# Elyntro Model Lab

This folder is a research scaffold, not a trained model or a production training pipeline.

## Chosen strategy

Elyntro's current strategy is to adapt a compatible pretrained open-weight model. Training a foundation model from scratch is not planned under the current hardware and zero-budget constraints.

The local inference foundation is the provider-neutral model gateway plus `elyntro.LocalLlamaCppAdapter`, which calls a local llama.cpp OpenAI-compatible endpoint. This adapter is a model-call component; it does not itself connect the Flask routes, train weights, or guarantee that the current model is licensed for every intended use.

## Before training

1. Reproduce the current engine and model tests.
2. Revalidate the local llama.cpp runtime and model file.
3. Confirm the exact base model license and tokenizer/architecture.
4. Build a repeatable benchmark and identify repeated model-specific failures.
5. Curate licensed, reviewed training examples without secrets or personal data.
6. Only then evaluate LoRA/QLoRA on compatible compute that is genuinely available at zero cost.

Do not train on data without permission or expose secrets/personal data in a corpus. Do not claim an adapter has been trained until a real artifact exists and has passed held-out evaluation.

See [OPEN_MODEL_ADAPTATION_PLAN.md](../../docs/OPEN_MODEL_ADAPTATION_PLAN.md) for the staged plan and acceptance criteria.
