"""
Victor Platform — Unified API Server
Serves the premium dashboard and exposes REST + SSE endpoints.
"""
from __future__ import annotations

import os
import sys
import json
import time
import sqlite3
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

# ── Load env (Victor OS .env first, then MyAIComputer .env) ──────────────────
_base = Path(__file__).parent
_victor_env = _base.parent / "victor_os" / ".env"
_myai_env   = _base.parent.parent / "MyAIComputer" / ".env"
if _victor_env.exists():  load_dotenv(_victor_env)
if _myai_env.exists():    load_dotenv(_myai_env)

# ── Victor OS sys.path ────────────────────────────────────────────────────────
_victor_os = str(_base.parent / "victor_os")
if _victor_os not in sys.path:
    sys.path.insert(0, _victor_os)

app = FastAPI(title="Victor Platform", docs_url=None, redoc_url=None)

templates = Jinja2Templates(directory=str(_base / "templates"))

# ── Static files (optional, create dir if absent) ────────────────────────────
_static = _base / "static"
_static.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_static)), name="static")


# ══════════════════════════════════════════════════════════════════════════════
# DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


# ══════════════════════════════════════════════════════════════════════════════
# SYSTEM STATUS
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/api/status")
async def status():
    try:
        import psutil
        cpu  = psutil.cpu_percent(interval=0.2)
        ram  = psutil.virtual_memory().percent
        disk = psutil.disk_usage('/').percent
    except Exception:
        cpu, ram, disk = 0, 0, 0

    # Count memory records
    mem_count = 0
    mem_db = _base.parent / "victor_os" / "memory_store" / "victor_memory.db"
    try:
        conn = sqlite3.connect(str(mem_db))
        cur = conn.execute("SELECT COUNT(*) FROM memories")
        mem_count = cur.fetchone()[0]
        conn.close()
    except Exception:
        pass

    # Last build
    last_build = ""
    last_build_path = _base.parent / "victor_os" / "memory_store" / "last_build.md"
    if last_build_path.exists():
        last_build = last_build_path.read_text(encoding="utf-8")[:200] + "..."

    return JSONResponse({
        "cpu": cpu, "ram": ram, "disk": disk,
        "memory_records": mem_count,
        "last_build_preview": last_build,
        "uptime": time.strftime("%Y-%m-%d %H:%M:%S"),
        "agents": [
            {"name": "Claude Sonnet 4.6", "role": "Architect", "status": "ready", "color": "#a78bfa"},
            {"name": "Gemini 3.1 Pro",    "role": "Designer",   "status": "ready", "color": "#34d399"},
            {"name": "GPT-4o",            "role": "Developer",  "status": "ready", "color": "#60a5fa"},
        ]
    })


# ══════════════════════════════════════════════════════════════════════════════
# BUILD — SSE streaming endpoint
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/api/build")
async def build_sse(idea: str = ""):
    if not idea.strip():
        return JSONResponse({"error": "No idea provided"}, status_code=400)

    from crew_engine import run_crew_streaming

    def _generate():
        yield "data: STARTED\n\n"
        for chunk in run_crew_streaming(idea):
            yield chunk
        yield "data: COMPLETE\n\n"

    return StreamingResponse(_generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ══════════════════════════════════════════════════════════════════════════════
# MEMORY — recent entries
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/api/memory")
async def get_memory(limit: int = 20):
    rows = []
    mem_db = _base.parent / "victor_os" / "memory_store" / "victor_memory.db"
    try:
        conn = sqlite3.connect(str(mem_db))
        cur = conn.execute(
            "SELECT id, text, memory_type, created_at FROM memories ORDER BY created_at DESC LIMIT ?",
            (limit,)
        )
        for row in cur.fetchall():
            rows.append({"id": row[0], "text": row[1][:120], "type": row[2], "created_at": row[3]})
        conn.close()
    except Exception as e:
        rows = [{"id": "err", "text": str(e), "type": "error", "created_at": ""}]
    return JSONResponse({"memories": rows})


# ══════════════════════════════════════════════════════════════════════════════
# HEALTH
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/health")
async def health():
    return {"status": "ok", "platform": "Victor OS + CrewAI", "version": "3.0.0"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
