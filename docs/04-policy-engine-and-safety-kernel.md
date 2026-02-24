# Policy Engine And Safety Kernel

Policy tiers:
- `ALLOW_AUTO`
- `ALLOW_WITH_LOG`
- `REQUIRE_APPROVAL`
- `DENY`

Hard deny zones:
- Password/credentials stores.
- Banking/payment targets.
- OS credential stores.

Controls:
- Global kill switch.
- Session safe mode.
- Auto downgrade to safe mode after repeated violations.

Audit:
- All decisions persisted in `policy_decisions`.
