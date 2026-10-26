# Elyntro GPU preflight

Run this check on the actual rented GPU machine before downloading training data or starting a long job.

```bash
python -m pip install -r requirements-model.txt
python -m elyntro_model.gpu_preflight --out reports/gpu-preflight.json
```

The command reports the Python/PyTorch versions, CUDA availability, detected GPU names and VRAM, host RAM where available, free disk space, and NVIDIA driver information when `nvidia-smi` is installed. By default it exits with code 2 if CUDA is unavailable, system RAM is below 16 GB, free disk is below 20 GB, or the detected GPU has less than 16 GB VRAM.

For the recommended first experiment, run with explicit targets:

```bash
python -m elyntro_model.gpu_preflight \
  --min-vram-gb 24 \
  --min-ram-gb 24 \
  --min-disk-gb 50 \
  --out reports/gpu-preflight.json
```

CPU-only local testing is allowed with `--allow-cpu`; do not use that flag to approve a paid GPU training run.

## Before launching

- Confirm the cloud provider, final quote, maximum runtime, and total spending cap with the authorized account owner.
- Ensure the machine has no public inbound ports unless specifically required and secured.
- Do not place API keys in Git or reports.
- Start with a short smoke test and a hard stop time. This preflight script cannot enforce a cloud spending cap or shut down a cloud instance.
- After training, verify the checkpoint and evaluation report were copied to durable storage before stopping/deleting the instance.

This is a hardware readiness check, not a model quality benchmark. Passing it does not prove that training will fit in VRAM or that the resulting model is useful.
