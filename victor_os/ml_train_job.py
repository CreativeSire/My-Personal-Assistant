from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path

import torch
import torch.nn as nn
import torch.optim as optim

from training_pipeline import TrainingPipeline


def _featurize(text: str, dim: int = 256) -> torch.Tensor:
    vec = torch.zeros(dim, dtype=torch.float32)
    for token in str(text or "").lower().split():
        idx = int(hashlib.sha256(token.encode("utf-8")).hexdigest(), 16) % dim
        vec[idx] += 1.0
    if vec.sum() > 0:
        vec = vec / vec.sum()
    return vec


def _load_jsonl(path: Path) -> list[dict]:
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Train small local routine model from exported dataset.")
    parser.add_argument("--export-dir", default=str(Path(__file__).resolve().parent / "memory_store" / "training_exports"))
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--lr", type=float, default=0.01)
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    args = parser.parse_args()

    export_dir = Path(args.export_dir)
    export_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = export_dir / "manifest.json"
    tp = TrainingPipeline()
    if not manifest_path.exists():
        tp.export_dataset(out_dir=str(export_dir), eval_ratio=0.2)

    train_rows = _load_jsonl(export_dir / "train.jsonl")
    eval_rows = _load_jsonl(export_dir / "eval.jsonl")
    if not train_rows:
        raise SystemExit("No training examples available.")

    labels = sorted({str(r.get("label") or "unknown") for r in train_rows})
    label_to_idx = {l: i for i, l in enumerate(labels)}

    x_train = torch.stack([_featurize(r.get("input", "")) for r in train_rows])
    y_train = torch.tensor([label_to_idx[str(r.get("label") or "unknown")] for r in train_rows], dtype=torch.long)
    x_eval = torch.stack([_featurize(r.get("input", "")) for r in eval_rows]) if eval_rows else torch.empty((0, 256))
    y_eval = (
        torch.tensor([label_to_idx.get(str(r.get("label") or "unknown"), 0) for r in eval_rows], dtype=torch.long)
        if eval_rows
        else torch.empty((0,), dtype=torch.long)
    )

    use_cuda = args.device == "cuda" or (args.device == "auto" and torch.cuda.is_available())
    device = torch.device("cuda" if use_cuda and torch.cuda.is_available() else "cpu")
    x_train = x_train.to(device)
    y_train = y_train.to(device)
    x_eval = x_eval.to(device)
    y_eval = y_eval.to(device)

    model = nn.Sequential(nn.Linear(256, 64), nn.ReLU(), nn.Linear(64, len(labels))).to(device)
    loss_fn = nn.CrossEntropyLoss()
    opt = optim.Adam(model.parameters(), lr=float(args.lr))

    model.train()
    for _ in range(max(1, args.epochs)):
        logits = model(x_train)
        loss = loss_fn(logits, y_train)
        opt.zero_grad()
        loss.backward()
        opt.step()

    model.eval()
    with torch.no_grad():
        train_pred = model(x_train).argmax(dim=1)
        train_acc = float((train_pred == y_train).float().mean().item())
        if len(eval_rows) > 0:
            eval_pred = model(x_eval).argmax(dim=1)
            eval_acc = float((eval_pred == y_eval).float().mean().item())
        else:
            eval_acc = 0.0

    checkpoint_id = f"ckpt_local_{int(time.time())}"
    ckpt_dir = Path(__file__).resolve().parent / "memory_store" / "checkpoints"
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    ckpt_path = ckpt_dir / f"{checkpoint_id}.pt"
    torch.save(
        {
            "checkpoint_id": checkpoint_id,
            "model_state_dict": model.state_dict(),
            "label_to_idx": label_to_idx,
            "idx_to_label": {v: k for k, v in label_to_idx.items()},
            "train_acc": train_acc,
            "eval_acc": eval_acc,
            "created_at": time.time(),
        },
        ckpt_path,
    )

    manifest_hash = ""
    if manifest_path.exists():
        manifest_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    metrics = {
        "checkpoint_id": checkpoint_id,
        "checkpoint_path": str(ckpt_path),
        "train_accuracy": train_acc,
        "accuracy": eval_acc,
        "eval_accuracy": eval_acc,
        "device": str(device),
        "train_examples": len(train_rows),
        "eval_examples": len(eval_rows),
        "training_manifest_hash": manifest_hash,
        "evaluated_at": time.time(),
    }
    out = Path(__file__).resolve().parent.parent / "docs" / "reports" / "training_job_latest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(f"wrote={out}")
    print(json.dumps(metrics, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
