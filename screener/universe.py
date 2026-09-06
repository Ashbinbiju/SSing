"""
Tradable universes, built from the Upstox NSE instrument master.

* fno   - the 210 NSE F&O underlyings. Default, because a stock with a
          futures contract has a liquidity floor worth screening.
* nse   - every NSE cash-segment company share (ISIN starting INE, which
          filters out SDLs, G-secs, ETFs and mutual-fund units).
* custom- whatever symbols you type in.
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import requests

from .upstox import DATA_DIR

ROOT = Path(__file__).resolve().parent.parent
MASTER = DATA_DIR / "NSE.csv.gz"
MASTER_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.csv.gz"

_FUT = re.compile(r"^(.*?)\d{2}[A-Z]{3}FUT$")

_cache: dict[str, pd.DataFrame] = {}


def master() -> pd.DataFrame:
    if "master" not in _cache:
        if not MASTER.exists():
            MASTER.parent.mkdir(parents=True, exist_ok=True)
            r = requests.get(MASTER_URL, timeout=60)
            r.raise_for_status()
            MASTER.write_bytes(r.content)
        _cache["master"] = pd.read_csv(MASTER, low_memory=False)
    return _cache["master"]


# Characters 8-9 of an Indian ISIN are the security type. The NSE_EQ segment
# lists far more than shares: of 4,794 INE instruments only 3,149 are actually
# equity. The rest are NCDs ("07", 1,148 of them - 0ABCL31, 1003SCFL31),
# bonds ("08", 450 - 737IRFC29), municipal debt ("24") and rights
# entitlements ("20"), none of which have tradable candle history.
SECURITY_TYPES = {
    "01": "share",
    "23": "invit",   # IRBINVIT, NHIT, TVSINVIT ...
    "25": "reit",    # EMBASSY, MINDSPACE, BIRET ...
}


def _equities(kinds=("share",)) -> pd.DataFrame:
    """NSE cash-segment instruments of the requested kinds, one row each.

    Filtering on the ISIN security type also settles the symbols that carry
    more than one ISIN because the company has listed debt as well
    (MOTHERSON has INE775A08105 alongside INE775A01035).
    """
    m = master()
    eq = m[(m["exchange"] == "NSE_EQ") & m["instrument_key"].str.contains(r"\|INE", regex=True)].copy()
    eq["_kind"] = eq["instrument_key"].str.split("|").str[-1].str[7:9].map(SECURITY_TYPES)
    eq = eq[eq["_kind"].isin(kinds)]
    return eq[["instrument_key", "tradingsymbol", "name"]].drop_duplicates("tradingsymbol", keep="first")


def fno() -> pd.DataFrame:
    m = master()
    fut = m[(m["exchange"] == "NSE_FO") & (m["instrument_type"] == "FUTSTK")]
    roots = set()
    for sym in fut["tradingsymbol"].dropna().astype(str):
        hit = _FUT.match(sym)
        if hit:
            roots.add(hit.group(1))
    eq = _equities()
    out = eq[eq["tradingsymbol"].isin(roots)].copy()
    return out.sort_values("tradingsymbol").reset_index(drop=True)


def nse() -> pd.DataFrame:
    return _equities().sort_values("tradingsymbol").reset_index(drop=True)


def custom(symbols) -> pd.DataFrame:
    wanted = {str(s).strip().upper() for s in symbols if str(s).strip()}
    eq = _equities()
    out = eq[eq["tradingsymbol"].str.upper().isin(wanted)].copy()
    return out.sort_values("tradingsymbol").reset_index(drop=True)


PRESETS = {"fno": fno, "nse": nse}


def resolve(name: str = "fno", symbols=None) -> pd.DataFrame:
    if symbols:
        return custom(symbols)
    fn = PRESETS.get(name)
    if fn is None:
        raise ValueError(f"unknown universe {name!r}; use one of {sorted(PRESETS)} or pass symbols")
    return fn()


def describe() -> list[dict]:
    return [
        {"id": "fno", "label": "F&O underlyings", "count": len(fno()),
         "note": "NSE futures underlyings - liquid, the default"},
        {"id": "nse", "label": "All NSE equity", "count": len(nse()),
         "note": "Every cash-segment company share. First scan is slow."},
    ]
