"""Report GPU/host readiness before starting a paid Elyntro training run.

This command is a preflight check only: it does not provision resources, download data,
start training, or incur cloud charges.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _memory_gb_linux():
    path = Path("/proc/meminfo")
    if not path.exists():
        return None
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition(":")
        if key in {"MemTotal", "MemAvailable"}:
            try:
                values[key] = int(value.strip().split()[0]) / (1024 ** 2)
            except (ValueError, IndexError):
                pass
    return {
        "total_gb": round(values["MemTotal"], 2) if "MemTotal" in values else None,
        "available_gb": round(values["MemAvailable"], 2) if "MemAvailable" in values else None,
    }


def inspect_hardware(torch_module=None, disk_path="."):
    disk = shutil.disk_usage(disk_path)
    report = {
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "cpu_count": os.cpu_count(),
        "ram": _memory_gb_linux(),
        "disk_path": str(Path(disk_path).resolve()),
        "disk_free_gb": round(disk.free / (1024 ** 3), 2),
        "cuda_available": False,
        "gpu_count": 0,
        "gpus": [],
    }
    try:
        if torch_module is None:
            import torch as torch_module
        report["pytorch_version"] = torch_module.__version__
        report["cuda_build"] = getattr(torch_module.version, "cuda", None)
        report["cuda_available"] = bool(torch_module.cuda.is_available())
        if report["cuda_available"]:
            report["gpu_count"] = torch_module.cuda.device_count()
            for index in range(report["gpu_count"]):
                props = torch_module.cuda.get_device_properties(index)
                try:
                    free_bytes, total_bytes = torch_module.cuda.mem_get_info(index)
                except TypeError:
                    with torch_module.cuda.device(index):
                        free_bytes, total_bytes = torch_module.cuda.mem_get_info()
                report["gpus"].append({
                    "index": index,
                    "name": props.name,
                    "vram_total_gb": round(total_bytes / (1024 ** 3), 2),
                    "vram_free_gb": round(free_bytes / (1024 ** 3), 2),
                })
    except ImportError:
        report["pytorch_version"] = None
        report["pytorch_error"] = "PyTorch is not installed"
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        report["nvidia_smi"] = result.stdout.strip() if result.returncode == 0 else None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        report["nvidia_smi"] = None
    return report


def evaluate(report, min_vram_gb=16, min_ram_gb=16, min_disk_gb=20, require_cuda=True):
    failures = []
    if require_cuda and not report.get("cuda_available"):
        failures.append("CUDA is unavailable; install a compatible NVIDIA driver and CUDA-enabled PyTorch build.")
    gpus = report.get("gpus") or []
    if require_cuda and gpus and max(g.get("vram_total_gb", 0) for g in gpus) < min_vram_gb:
        failures.append(f"No detected GPU meets the {min_vram_gb} GB VRAM minimum.")
    ram = report.get("ram") or {}
    if ram.get("total_gb") is not None and ram["total_gb"] < min_ram_gb:
        failures.append(f"System RAM is below the {min_ram_gb} GB minimum.")
    if report.get("disk_free_gb", 0) < min_disk_gb:
        failures.append(f"Free disk space is below the {min_disk_gb} GB minimum.")
    return {"ready": not failures, "failures": failures}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-vram-gb", type=float, default=16)
    parser.add_argument("--min-ram-gb", type=float, default=16)
    parser.add_argument("--min-disk-gb", type=float, default=20)
    parser.add_argument("--allow-cpu", action="store_true", help="Allow a CPU-only host for local tests.")
    parser.add_argument("--disk-path", default=".")
    parser.add_argument("--out", type=Path, help="Optional JSON report path.")
    args = parser.parse_args()
    report = inspect_hardware(disk_path=args.disk_path)
    report["checks"] = evaluate(
        report, min_vram_gb=args.min_vram_gb, min_ram_gb=args.min_ram_gb,
        min_disk_gb=args.min_disk_gb, require_cuda=not args.allow_cpu,
    )
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    print(encoded)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(encoded + "\n", encoding="utf-8")
    raise SystemExit(0 if report["checks"]["ready"] else 2)


if __name__ == "__main__":
    main()
