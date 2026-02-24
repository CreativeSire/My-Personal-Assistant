# Evaluation And Promotion Gates

Required checks:
- Safety compliance pass.
- Regression test pass.
- No duplicate delivery regressions.
- Policy false-negative rate within threshold.
- Quality non-degradation on golden set.

Promotion policy:
- Candidate model runs in shadow mode first.
- Promote only after acceptance metrics hold for defined window.
