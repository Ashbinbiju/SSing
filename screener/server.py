"""
Local API + UI server for the screener.

    python screen.py serve          ->  http://127.0.0.1:8777

Single user, single machine, so one scan job at a time is plenty. The
last finished scan is written to disk and reloaded on start, so opening
the page shows results immediately instead of an empty table.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import scan as scanner
from . import universe as un
from . import upstox as ux
from .strategy import Params

ROOT = Path(__file__).resolve().parent.parent
WEB = Path(__file__).resolve().parent / "web"
STATE = ux.DATA_DIR / "cache" / "last_scan.json"

app = FastAPI(title="1H Swing Screener", docs_url=None, redoc_url=None)

# --------------------------------------------------------------- access
# Deployed on a public URL this app is worth protecting: anyone who finds
# it can start scans that burn the Upstox rate limit against your token.
# Set SCREENER_ACCESS_KEY and every request must carry it, as an
# `X-Screener-Key` header or a `?key=` parameter. Unset, nothing changes.
ACCESS_KEY = os.environ.get("SCREENER_ACCESS_KEY", "").strip()

# Only needed when the UI is served from somewhere else (Vercel) and the
# API lives here. Comma-separated origins, or "*" for any.
ORIGINS = [o.strip() for o in os.environ.get("SCREENER_CORS_ORIGINS", "").split(",") if o.strip()]
if ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ORIGINS,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )


@app.middleware("http")
async def gate(request: Request, call_next):
    if ACCESS_KEY and request.method != "OPTIONS" and request.url.path not in ("/api/health",):
        given = request.headers.get("x-screener-key") or request.query_params.get("key", "")
        if given != ACCESS_KEY:
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
    return await call_next(request)

_lock = threading.Lock()
_progress = scanner.Progress()
_last: dict = {"rows": [], "meta": {}}
_params = Params()


def _load_state():
    global _last, _params
    if STATE.exists():
        try:
            blob = json.loads(STATE.read_text())
            _last = {"rows": blob.get("rows", []), "meta": blob.get("meta", {})}
            _params = Params.from_dict(blob.get("meta", {}).get("params"))
        except Exception:
            pass


def _save_state():
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(_last))
    except Exception:
        pass


_load_state()


class ScanRequest(BaseModel):
    universe: str = "fno"
    symbols: list[str] | None = None
    lookback: int = 3
    months: int = 6
    refresh: bool = True
    params: dict | None = None
    # The UI leaves these at zero and filters in the browser instead, so
    # loosening the floor never needs a rescan. Here for API/CLI parity.
    min_price: float = 0.0
    min_turnover_cr: float = 0.0


@app.get("/api/config")
def config():
    return {
        "universes": un.describe(),
        "defaults": Params().to_dict(),
        "params": _params.to_dict(),
        "options": {
            "macdConfirmationMode": ["MACD Above Signal", "MACD Bullish Cross", "Either"],
            "signalMode": ["EMA Cross + MACD", "MACD Divergence + EMA", "Either"],
        },
        "timeframe": "1H",
    }


@app.get("/api/status")
def status():
    snap = _progress.snapshot()
    snap["has_results"] = bool(_last["rows"])
    snap["meta"] = _last.get("meta", {})
    return snap


@app.get("/api/results")
def results():
    return {"rows": _last["rows"], "meta": _last.get("meta", {})}


def _run_scan(req: ScanRequest):
    global _last, _params
    p = Params.from_dict(req.params)
    _params = p
    started = time.time()
    try:
        rows = scanner.run(
            universe_id=req.universe,
            symbols=req.symbols,
            params=p,
            lookback=req.lookback,
            months=req.months,
            refresh=req.refresh,
            progress=_progress,
            min_price=req.min_price,
            min_turnover_cr=req.min_turnover_cr,
        )
        snap = _progress.snapshot()
        with _progress.lock:
            reasons: dict[str, int] = {}
            for e in _progress.errors:
                msg = e.get("error", "")
                if "no candle history" in msg:
                    key = "no candle history"
                elif "needs" in msg:
                    key = "too little history"
                else:
                    key = msg.split(":")[0] or "error"
                reasons[key] = reasons.get(key, 0) + 1
        _last = {
            "rows": rows,
            "meta": {
                "universe": req.universe,
                "lookback": req.lookback,
                "params": p.to_dict(),
                "scanned": snap["ok"],
                "failed": snap["failed"],
                "skips": sorted(reasons.items(), key=lambda kv: -kv[1]),
                "seconds": round(time.time() - started, 1),
                "finished_at": time.time(),
                "bar_time": rows[0]["bar_time"] if rows else None,
            },
        }
        _save_state()
    except Exception as exc:
        with _progress.lock:
            _progress.state = "error"
            _progress.message = f"{type(exc).__name__}: {exc}"[:300]
    finally:
        with _progress.lock:
            _progress.finished = time.time()
            if _progress.state == "running":
                _progress.state = "done"


@app.post("/api/scan")
def start_scan(req: ScanRequest):
    with _lock:
        if _progress.snapshot()["state"] == "running":
            raise HTTPException(409, "a scan is already running")
        with _progress.lock:
            _progress.state = "running"
            _progress.message = ""
            _progress.started = time.time()
            _progress.total = 0
            _progress.done = 0
    threading.Thread(target=_run_scan, args=(req,), daemon=True).start()
    return {"ok": True}


@app.get("/api/chart")
def chart(key: str, bars: int = 220):
    try:
        return scanner.series_for(key, _params, bars=bars)
    except Exception as exc:
        raise HTTPException(400, f"{type(exc).__name__}: {exc}")


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


app.mount("/static", StaticFiles(directory=str(WEB)), name="static")


@app.get("/api/health")
def health():
    ok = True
    detail = "ready"
    try:
        ux.token()
    except Exception as exc:
        ok, detail = False, str(exc)
    return JSONResponse({"ok": ok, "detail": detail, "market_date": str(ux.today_ist())},
                        status_code=200 if ok else 503)
