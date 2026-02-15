## Victor ERP Relay Service

Run locally:

```powershell
uvicorn relay_service.main:app --host 0.0.0.0 --port 8090
```

Auth headers required on write/pull/ack:

- `X-Device-Id`
- `X-Timestamp` (unix seconds)
- `X-Nonce`
- `X-Signature` (`HMAC_SHA256(secret, device:timestamp:nonce:sha256(body))`)

Current implementation uses SQLite dev storage by default for local testing.
Set `RELAY_DATABASE_URL` and replace `_conn()` implementation for Postgres production deployment.

