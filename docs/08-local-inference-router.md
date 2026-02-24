# Local Inference Router

Routing rule:
- If task is routine and confidence >= threshold, route to local model.
- Otherwise route to frontier/API model.

Phase boundary:
- Introduce classifier/router at end of Phase 2.

Initial local tasks:
- Intent routing.
- Risk prediction.
- Tone profile selection.
