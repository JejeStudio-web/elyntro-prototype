from elyntro_model.gpu_preflight import evaluate


def test_preflight_rejects_missing_cuda_by_default():
    report = {"cuda_available": False, "gpus": [], "ram": {"total_gb": 32}, "disk_free_gb": 100}
    result = evaluate(report)
    assert result["ready"] is False
    assert any("CUDA is unavailable" in failure for failure in result["failures"])


def test_preflight_accepts_host_meeting_gpu_ram_and_disk_targets():
    report = {
        "cuda_available": True,
        "gpus": [{"name": "Test GPU", "vram_total_gb": 24, "vram_free_gb": 20}],
        "ram": {"total_gb": 32},
        "disk_free_gb": 80,
    }
    assert evaluate(report)["ready"] is True


def test_preflight_rejects_insufficient_vram_ram_and_disk():
    report = {
        "cuda_available": True,
        "gpus": [{"name": "Small GPU", "vram_total_gb": 8, "vram_free_gb": 7}],
        "ram": {"total_gb": 8},
        "disk_free_gb": 5,
    }
    result = evaluate(report, min_vram_gb=16, min_ram_gb=16, min_disk_gb=20)
    assert result["ready"] is False
    assert len(result["failures"]) == 3


def test_preflight_can_allow_cpu_for_local_tests():
    report = {"cuda_available": False, "gpus": [], "ram": {"total_gb": 16}, "disk_free_gb": 30}
    assert evaluate(report, require_cuda=False)["ready"] is True
