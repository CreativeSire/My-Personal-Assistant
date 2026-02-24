from __future__ import annotations

import json
import platform
import subprocess
from pathlib import Path

import torch


def _nvidia_smi() -> str:
    try:
        out = subprocess.check_output(["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"], text=True)
        return out.strip()
    except Exception:
        return ""


def main() -> int:
    smi = _nvidia_smi()
    report = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "torch_version": torch.__version__,
        "cuda_available": bool(torch.cuda.is_available()),
        "cuda_version": torch.version.cuda,
        "cuda_device_count": int(torch.cuda.device_count()),
        "nvidia_smi": smi,
        "recommended_next": [],
    }
    if not report["cuda_available"]:
        report["recommended_next"].append(
            "If NVIDIA GPU exists, install CUDA-enabled PyTorch wheel (cu121/cu124) and matching driver."
        )
        report["recommended_next"].append(
            "Example: pip uninstall -y torch torchvision torchaudio && pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124"
        )
    else:
        report["recommended_next"].append("GPU is ready. Use --device cuda for training jobs.")

    out = Path(__file__).resolve().parent.parent / "docs" / "reports" / "gpu_setup_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"wrote={out}")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
