"""
Upstox v3 market-data client.

Only the historical/intraday candle endpoints are used - they need no
account permissions, which matters because the supplied token is
IP-restricted and the authenticated endpoints return UDAPI1221.

Candles are cached as parquet and refreshed incrementally, so the first
scan of a universe is the slow one and every later scan only asks for the
bars that appeared since.
"""
from __future__ import annotations

import os
import threading
import time
import urllib.parse
from collections import deque
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache" / "hourly"
CACHE.mkdir(parents=True, exist_ok=True)

BASE = "https://api.upstox.com/v3/historical-candle"
IST = "Asia/Kolkata"

# Upstox caps a single `hours` request at one quarter.
MAX_WINDOW_DAYS = 90

COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]

# NSE cash session, minutes past midnight IST.
SESSION_OPEN_MIN = 9 * 60 + 15     # 09:15
SESSION_CLOSE_MIN = 15 * 60 + 30   # 15:30

# The session runs 375 minutes, so an hourly grid leaves a 15-minute
# remainder. Upstox returns that remainder as its own 15:15 candle;
# TradingView does not - it extends the 14:15 bar to the close, giving six
# bars a session. Matching that matters: on INOXWIND the seven-bar series
# puts ADX at 50.10 where the chart reads 43.5, and folding the stub
# reproduces 43.51. Set SCREENER_FOLD_STUB=0 to keep Upstox's own bars.
STUB_START_MIN = SESSION_OPEN_MIN + ((SESSION_CLOSE_MIN - SESSION_OPEN_MIN) // 60) * 60  # 15:15


def fold_stub_default() -> bool:
    _load_env()
    return os.environ.get("SCREENER_FOLD_STUB", "1").strip() not in ("0", "false", "False")


def fold_session_stub(df: pd.DataFrame) -> pd.DataFrame:
    """Merge each session's short closing candle into the bar before it."""
    if df.empty:
        return df
    o = df.reset_index(drop=True)
    ts = o["timestamp"]
    mins = (ts.dt.hour * 60 + ts.dt.minute).to_numpy()
    dates = ts.dt.date.to_numpy()

    stub = np.flatnonzero(mins == STUB_START_MIN)
    stub = stub[stub > 0]
    prev = stub - 1
    # only fold into the 14:15 bar of the same session
    pair = (dates[prev] == dates[stub]) & (mins[prev] == STUB_START_MIN - 60)
    src, dst = stub[pair], prev[pair]
    if not len(src):
        return o

    high = o["high"].to_numpy(copy=True)
    low = o["low"].to_numpy(copy=True)
    close = o["close"].to_numpy(copy=True)
    vol = o["volume"].to_numpy(copy=True)

    high[dst] = np.maximum(high[dst], high[src])
    low[dst] = np.minimum(low[dst], low[src])
    close[dst] = close[src]
    vol[dst] = vol[dst] + vol[src]

    o["high"], o["low"], o["close"], o["volume"] = high, low, close, vol
    keep = np.ones(len(o), dtype=bool)
    keep[src] = False
    return o[keep].reset_index(drop=True)


# ---------------------------------------------------------------- token


def _load_env():
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def token() -> str:
    _load_env()
    t = os.environ.get("UPSTOX_TOKEN", "").strip()
    if not t:
        raise RuntimeError("UPSTOX_TOKEN is not set (put it in .env)")
    return t


# ------------------------------------------------------------ throttle


class _Throttle:
    """Token bucket sized well under the published Upstox limits."""

    def __init__(self, per_second: int = 12, per_minute: int = 380):
        self.per_second = per_second
        self.per_minute = per_minute
        self._hits: deque[float] = deque()
        self._lock = threading.Lock()

    def wait(self):
        while True:
            with self._lock:
                now = time.monotonic()
                while self._hits and now - self._hits[0] > 60:
                    self._hits.popleft()
                last_sec = sum(1 for t in self._hits if now - t < 1.0)
                if last_sec < self.per_second and len(self._hits) < self.per_minute:
                    self._hits.append(now)
                    return
                sleep_for = 0.05 if last_sec >= self.per_second else max(
                    0.05, 60 - (now - self._hits[0])
                )
            time.sleep(min(sleep_for, 1.0))


THROTTLE = _Throttle()

_session_local = threading.local()


def _session() -> requests.Session:
    s = getattr(_session_local, "s", None)
    if s is None:
        s = requests.Session()
        s.headers.update({"Authorization": f"Bearer {token()}", "Accept": "application/json"})
        _session_local.s = s
    return s


class UpstoxError(RuntimeError):
    pass


def _get(url: str, tries: int = 4):
    delay = 0.6
    last = None
    for _ in range(tries):
        THROTTLE.wait()
        try:
            r = _session().get(url, timeout=30)
        except requests.RequestException as exc:
            last = str(exc)
            time.sleep(delay)
            delay *= 2
            continue
        if r.status_code == 200:
            return r.json().get("data", {}).get("candles") or []
        if r.status_code in (429, 500, 502, 503, 504):
            last = f"HTTP {r.status_code}"
            time.sleep(delay)
            delay *= 2
            continue
        # 400 with UDAPI1148 just means "no data for this range"
        try:
            errs = r.json().get("errors") or []
            code = errs[0].get("errorCode") if errs else ""
            msg = errs[0].get("message") if errs else r.text[:120]
        except Exception:
            code, msg = "", r.text[:120]
        if r.status_code == 400 and code in ("UDAPI1148", "UDAPI100011"):
            return []
        raise UpstoxError(f"{r.status_code} {code} {msg}")
    raise UpstoxError(last or "request failed")


# ------------------------------------------------------------- candles


def _empty() -> pd.DataFrame:
    """An empty candle frame that still carries the right dtypes.

    Concatenating a plain `DataFrame(columns=...)` with a real one turns the
    timestamp column into `object`, and every later `.dt` access then blows
    up - which is what happened on illiquid symbols whose history arrives in
    a mix of empty and non-empty chunks.
    """
    return pd.DataFrame({
        "timestamp": pd.Series(dtype=f"datetime64[ns, {IST}]"),
        "open": pd.Series(dtype="float64"),
        "high": pd.Series(dtype="float64"),
        "low": pd.Series(dtype="float64"),
        "close": pd.Series(dtype="float64"),
        "volume": pd.Series(dtype="float64"),
    })


def _frame(rows) -> pd.DataFrame:
    if not rows:
        return _empty()
    df = pd.DataFrame(rows).iloc[:, :6]
    df.columns = COLUMNS
    df["timestamp"] = pd.to_datetime(df["timestamp"], format="ISO8601", utc=True).dt.tz_convert(IST)
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)


def _fetch_range(key: str, start: date, end: date) -> pd.DataFrame:
    enc = urllib.parse.quote(key, safe="")
    parts = []
    cursor = start
    while cursor <= end:
        stop = min(cursor + timedelta(days=MAX_WINDOW_DAYS), end)
        rows = _get(f"{BASE}/{enc}/hours/1/{stop.isoformat()}/{cursor.isoformat()}")
        parts.append(_frame(rows))
        cursor = stop + timedelta(days=1)
    parts = [p for p in parts if not p.empty]
    if not parts:
        return _empty()
    return (
        pd.concat(parts, ignore_index=True)
        .sort_values("timestamp")
        .drop_duplicates("timestamp")
        .reset_index(drop=True)
    )


def _fetch_intraday(key: str) -> pd.DataFrame:
    enc = urllib.parse.quote(key, safe="")
    return _frame(_get(f"{BASE}/intraday/{enc}/hours/1"))


def _cache_path(key: str) -> Path:
    return CACHE / (key.replace("|", "_").replace("/", "_") + ".parquet")


# Newest bar seen from an actual network refresh in this process. A universe
# of 3,000 names would otherwise fire one "anything newer?" request per symbol
# on every rescan; once a few symbols have established that nothing is newer
# than, say, Friday 14:15, every symbol whose cache already reaches that can
# skip the round trip. Only symbols that really did refresh contribute, so a
# stale cache can never suppress a fetch.
_WATERMARK: dict = {"ts": None}
_WM_LOCK = threading.Lock()


def _watermark():
    with _WM_LOCK:
        return _WATERMARK["ts"]


def _bump_watermark(ts) -> None:
    with _WM_LOCK:
        cur = _WATERMARK["ts"]
        if cur is None or ts > cur:
            _WATERMARK["ts"] = ts


def reset_watermark() -> None:
    with _WM_LOCK:
        _WATERMARK["ts"] = None


def _session_live() -> bool:
    """Weekday, and past the 09:15 open in IST."""
    now = pd.Timestamp.now(tz=IST)
    if now.weekday() >= 5:
        return False
    return now >= now.normalize() + pd.Timedelta(hours=9, minutes=15)


def _bar_is_forming(ts: pd.Timestamp, folded: bool) -> bool:
    """True while the bar that opened at `ts` is still trading."""
    day = ts.normalize()
    session_end = day + pd.Timedelta(minutes=SESSION_CLOSE_MIN)
    close_at = ts + pd.Timedelta(hours=1)
    # a folded last bar runs all the way to the close
    if folded and close_at >= day + pd.Timedelta(minutes=STUB_START_MIN):
        close_at = session_end
    close_at = min(close_at, session_end)
    return pd.Timestamp.now(tz=IST) < close_at


def hourly(
    key: str,
    months: int = 6,
    use_cache: bool = True,
    refresh: bool = True,
    include_forming: bool = False,
    fold_stub: bool | None = None,
) -> pd.DataFrame:
    """1-hour candles for one instrument, oldest first.

    Bars are session aligned the way TradingView aligns them for NSE:
    09:15, 10:15, 11:15, 12:15, 13:15, 14:15 - six a session, with the last
    one running to the 15:30 close. See `fold_session_stub`.
    """
    if fold_stub is None:
        fold_stub = fold_stub_default()
    path = _cache_path(key)
    today = date.today()
    want_from = today - timedelta(days=int(months * 31))

    df = _empty()
    if use_cache and path.exists():
        try:
            df = pd.read_parquet(path)
            if not df.empty:
                df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(IST)
        except Exception:
            df = _empty()

    # nothing newer exists than what this cache already holds
    wm = _watermark()
    if refresh and wm is not None and not df.empty and df["timestamp"].iloc[-1] >= wm:
        refresh = False
    refreshed = refresh

    if df.empty:
        df = _fetch_range(key, want_from, today)
        refreshed = True
    elif refresh:
        last = df["timestamp"].iloc[-1].date()
        if last < today:
            fresh = _fetch_range(key, last, today)
            if not fresh.empty:
                df = (
                    pd.concat([df, fresh], ignore_index=True)
                    .sort_values("timestamp")
                    .drop_duplicates("timestamp", keep="last")
                    .reset_index(drop=True)
                )

    # The historical endpoint only publishes today once the session is over,
    # so pull today's bars from the intraday endpoint until it does.
    if refresh and _session_live() and (df.empty or df["timestamp"].iloc[-1].date() < today):
        try:
            intra = _fetch_intraday(key)
        except UpstoxError:
            intra = _empty()
        if not intra.empty:
            df = (
                pd.concat([df, intra], ignore_index=True)
                .sort_values("timestamp")
                .drop_duplicates("timestamp", keep="last")
                .reset_index(drop=True)
            )

    if df.empty:
        return df

    if refreshed:
        _bump_watermark(df["timestamp"].iloc[-1])

    # Today's bars are never cached: the newest one may still be forming, and
    # a half-built candle written to disk would never be corrected.
    if use_cache:
        try:
            past = df[df["timestamp"].dt.date < today]
            if not past.empty:
                path.parent.mkdir(parents=True, exist_ok=True)
                past.to_parquet(path, index=False)
        except Exception:
            pass

    if fold_stub:
        df = fold_session_stub(df)

    if not include_forming and _bar_is_forming(df["timestamp"].iloc[-1], fold_stub):
        df = df.iloc[:-1].reset_index(drop=True)

    cut = pd.Timestamp(want_from, tz=IST)
    return df[df["timestamp"] >= cut].reset_index(drop=True)


DAILY_CACHE = ROOT / "data" / "cache" / "daily"
DAILY_CACHE.mkdir(parents=True, exist_ok=True)


def daily(
    key: str,
    days: int = 560,
    use_cache: bool = True,
    refresh: bool = True,
    upto: date | None = None,
) -> pd.DataFrame:
    """Daily candles, cached, for the higher-timeframe trend column.

    `upto` is the newest session the caller already knows exists (from the
    hourly data). When the cache reaches it there is nothing to fetch, so on
    a warm scan this costs no requests at all.
    """
    path = DAILY_CACHE / (key.replace("|", "_").replace("/", "_") + ".parquet")
    df = _empty()
    if use_cache and path.exists():
        try:
            df = pd.read_parquet(path)
            if not df.empty:
                df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(IST)
        except Exception:
            df = _empty()

    target = upto or date.today()
    stale = df.empty or df["timestamp"].iloc[-1].date() < target
    if refresh and stale:
        enc = urllib.parse.quote(key, safe="")
        end = date.today()
        start = end - timedelta(days=days)
        fresh = _frame(_get(f"{BASE}/{enc}/days/1/{end.isoformat()}/{start.isoformat()}"))
        if not fresh.empty:
            df = fresh
            if use_cache:
                try:
                    df.to_parquet(path, index=False)
                except Exception:
                    pass
    return df
