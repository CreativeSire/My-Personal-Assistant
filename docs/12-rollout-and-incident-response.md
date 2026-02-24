# Rollout And Incident Response

Rollout stages:
1. Local-only canary.
2. Telegram + Desktop parity validation.
3. High-autonomy activation with safety kernel enabled.
4. Wider workflow activation.

Incident controls:
- Global kill switch.
- Session safe mode.
- Queue recovery on restart.
- Delivery dedupe to prevent repeated sends.

Critical drills:
- Crash recovery drill.
- Duplicate delivery replay drill.
- Hard-deny policy drill.
