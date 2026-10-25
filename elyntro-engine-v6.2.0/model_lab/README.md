# Elyntro Model Lab — placeholder

This folder is intentionally a research scaffold, not a claim that a useful model has already been trained.

A from-scratch language model requires a curated corpus, tokenizer, training loop, evaluation set, checkpointing, and suitable compute. The engine must stay model-agnostic so the orchestration core can be tested before a model exists.

Next planned modules:
1. `data_manifest.py`: dataset provenance, license, language, quality and filtering metadata.
2. `tokenizer.py`: train and version a tokenizer on a lawful, curated corpus.
3. `train.py`: small PyTorch causal language model for educational experiments only.
4. `evaluate.py`: held-out loss plus task-specific tests and safety evaluations.

Do not train on data without permission or expose secrets/personal data in a corpus.
