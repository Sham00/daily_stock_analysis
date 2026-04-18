#!/usr/bin/env python3
"""
Prescan wildcard universe and output a combined STOCK_LIST.

Reads:
  - PORTFOLIO env var (comma-separated, defaults to hardcoded list)
  - WILDCARD_UNIVERSE env var (comma-separated, defaults to hardcoded list)
  - WILDCARD_COUNT env var (default 3)

Outputs to stdout a single CSV line: portfolio + top N wildcards.
Usage in GitHub Actions:
  echo "STOCK_LIST=$(python scripts/prescan.py)" >> $GITHUB_ENV
"""
from __future__ import annotations

import os
import sys
import warnings
warnings.filterwarnings("ignore")

import pandas as pd
import yfinance as yf

# ── defaults ──────────────────────────────────────────────────────────────────

DEFAULT_PORTFOLIO = "MSTR,B,FCX,AGMRF,CSCCF,BTC-USD"

DEFAULT_WILDCARDS = (
    "AMD,AVGO,ARM,ANET,DELL,VRT,SMCI,PWR,ETN,"
    "DLR,AMT,CEG,VST,NRG,NEE,GEV,"
    "MARA,RIOT,CLSK,IREN,HUT,CORZ,"
    "NEM,MP,WPM,"
    "ET,TRGP,DVN,OXY,FANG"
)


def _csv(env_key: str, default: str) -> list[str]:
    raw = os.getenv(env_key, default)
    return [t.strip().upper() for t in raw.split(",") if t.strip()]


def _fetch_close(ticker: str) -> pd.Series | None:
    try:
        hist = yf.Ticker(ticker).history(period="3mo", interval="1d", auto_adjust=False)
        if hist.empty or len(hist) < 30:
            return None
        return hist["Close"].dropna()
    except Exception:
        return None


def _score(close: pd.Series) -> float:
    score = 0.0
    sma20 = close.rolling(20).mean().iloc[-1]
    sma50 = close.rolling(50).mean().iloc[-1] if len(close) >= 50 else None
    last = close.iloc[-1]
    ret5 = (close.iloc[-1] / close.iloc[-6] - 1) if len(close) >= 6 else 0
    ret20 = (close.iloc[-1] / close.iloc[-21] - 1) if len(close) >= 21 else 0

    if last > sma20:
        score += 30
    if sma50 is not None and sma20 > sma50:
        score += 30
    if ret5 > 0:
        score += 20
    if ret20 > 0:
        score += 20
    return score


def main() -> None:
    portfolio = _csv("PORTFOLIO", DEFAULT_PORTFOLIO)
    wildcards = [t for t in _csv("WILDCARD_UNIVERSE", DEFAULT_WILDCARDS) if t not in set(portfolio)]
    wildcard_count = int(os.getenv("WILDCARD_COUNT", "3"))

    scored: list[tuple[str, float]] = []
    for ticker in wildcards:
        close = _fetch_close(ticker)
        if close is None:
            continue
        scored.append((ticker, _score(close)))

    scored.sort(key=lambda x: x[1], reverse=True)
    promoted = [t for t, s in scored[:wildcard_count] if s >= 40]

    combined = portfolio + promoted
    print(",".join(combined))

    # Also write a human-readable note to stderr so GitHub Actions logs show what was promoted
    print(f"[prescan] Portfolio: {portfolio}", file=sys.stderr)
    print(f"[prescan] Promoted wildcards: {promoted}", file=sys.stderr)


if __name__ == "__main__":
    main()
