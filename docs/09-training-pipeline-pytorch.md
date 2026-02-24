# Training Pipeline (PyTorch)

Pipeline:
1. Export curated training examples from `events` + `corrections`.
2. Redact/minimize PII.
3. Build train/eval splits.
4. Fine-tune routine model.
5. Evaluate against golden set.

Constraints:
- Batch-cycle retraining only.
- No runtime self-rewrite.
- Promote only with safety/regression pass.
