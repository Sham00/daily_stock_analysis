#!/usr/bin/env python3
"""
Portfolio-first trade ticket generator.

Reads the same PORTFOLIO / WILDCARD_UNIVERSE / WILDCARD_COUNT env vars as prescan.py.
Generates rule-based trade tickets and sends them to Telegram.

Required env vars for Telegram delivery:
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID
"""
from __future__ import annotations

import json
import math
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import warnings
from pathlib import Path
warnings.filterwarnings("ignore")

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime
from zoneinfo import ZoneInfo

# ── config ────────────────────────────────────────────────────────────────────

DEFAULT_PORTFOLIO = "MSTR,B,FCX,AGMRF,CSCCF,BTC-USD"
DEFAULT_WILDCARDS = (
    "AMD,AVGO,ARM,ANET,DELL,VRT,SMCI,PWR,ETN,"
    "DLR,AMT,CEG,VST,NRG,NEE,GEV,"
    "MARA,RIOT,CLSK,IREN,HUT,CORZ,"
    "NEM,MP,WPM,"
    "ET,TRGP,DVN,OXY,FANG"
)

MIN_RSI = 50
MAX_RSI = 78
MAX_BREAKOUT_GAP = 0.03
MIN_RR = 1.5
MAX_ORDERS = 3
WILDCARD_COUNT = int(os.getenv("WILDCARD_COUNT", "3"))
PORTFOLIO_VALUE = float(os.getenv("PORTFOLIO_VALUE", "100000"))
RISK_PCT = float(os.getenv("RISK_PER_TRADE_PCT", "0.5"))
MAX_POS_PCT = float(os.getenv("MAX_POSITION_PCT", "5.0"))


def _csv(env_key: str, default: str) -> list[str]:
    raw = os.getenv(env_key, default)
    return [t.strip().upper() for t in raw.split(",") if t.strip()]


# ── indicators ────────────────────────────────────────────────────────────────

def _rsi(series: pd.Series, period: int = 14) -> float:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    ag = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    al = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = ag / al.replace(0, math.nan)
    rsi = (100 - 100 / (1 + rs)).fillna(50.0)
    return float(rsi.iloc[-1])


def _atr(frame: pd.DataFrame, period: int = 14) -> float:
    prev = frame["Close"].shift(1)
    tr = pd.concat([frame["High"] - frame["Low"], (frame["High"] - prev).abs(), (frame["Low"] - prev).abs()], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])


def _enrich(hist: pd.DataFrame) -> dict:
    c = hist["Close"]
    return {
        "close": float(c.iloc[-1]),
        "sma20": float(c.rolling(20).mean().iloc[-1]),
        "sma50": float(c.rolling(50).mean().iloc[-1]) if len(c) >= 50 else 0.0,
        "atr14": _atr(hist),
        "rsi14": _rsi(c),
        "ret5": float((c.iloc[-1] / c.iloc[-6] - 1)) if len(c) >= 6 else 0.0,
        "ret20": float((c.iloc[-1] / c.iloc[-21] - 1)) if len(c) >= 21 else 0.0,
        "breakout_gap": float(max(c.rolling(20).max().iloc[-1] - c.iloc[-1], 0) / c.iloc[-1]),
        "vol_ratio": float(hist["Volume"].iloc[-1] / hist["Volume"].rolling(20).mean().iloc[-1]) if hist["Volume"].rolling(20).mean().iloc[-1] > 0 else 1.0,
    }


def _fetch(ticker: str) -> dict | None:
    try:
        hist = yf.Ticker(ticker).history(period="6mo", interval="1d", auto_adjust=False)
        if hist.empty or len(hist) < 60:
            return None
        return _enrich(hist)
    except Exception:
        return None


# ── scoring ───────────────────────────────────────────────────────────────────

def _score(d: dict) -> tuple[float, list[str]]:
    score, reasons = 0.0, []
    if d["close"] > d["sma20"] > d["sma50"] > 0:
        score += 35; reasons.append("trend aligned")
    if d["ret5"] > 0:
        score += 15; reasons.append("5D momentum positive")
    if d["ret20"] > 0:
        score += 15; reasons.append("20D momentum positive")
    if 0 <= d["breakout_gap"] <= MAX_BREAKOUT_GAP:
        score += 15; reasons.append("near breakout")
    if d["vol_ratio"] >= 1.1:
        score += 10; reasons.append("volume confirmation")
    if MIN_RSI <= d["rsi14"] <= MAX_RSI:
        score += 10; reasons.append("RSI in healthy range")
    return score, reasons


def _qualifies(d: dict) -> tuple[bool, str]:
    if d["close"] <= d["sma20"]: return False, "close below SMA20"
    if d["sma20"] <= d["sma50"]: return False, "SMA20 not above SMA50"
    if d["ret5"] <= 0: return False, "5D momentum negative"
    if d["ret20"] <= 0: return False, "20D momentum negative"
    if not (MIN_RSI <= d["rsi14"] <= MAX_RSI): return False, "RSI outside trade range"
    if d["breakout_gap"] > MAX_BREAKOUT_GAP: return False, "too far from breakout"
    if d["atr14"] <= 0: return False, "ATR unavailable"
    return True, "qualified"


def _ticket(ticker: str, d: dict, score: float, reasons: list[str], label: str) -> dict:
    stop_buf = max(d["atr14"] * 2.0, d["close"] * 0.015)
    stop = round(d["close"] - stop_buf, 2)
    risk = max(d["close"] - stop, 0.01)
    risk_budget = PORTFOLIO_VALUE * RISK_PCT / 100
    max_val = PORTFOLIO_VALUE * MAX_POS_PCT / 100
    shares = max(1.0, min(risk_budget / risk, max_val / d["close"]))
    size_pct = min(MAX_POS_PCT, shares * d["close"] / PORTFOLIO_VALUE * 100)
    t1 = round(d["close"] + 1.5 * risk, 2)
    t2 = round(d["close"] + 2.5 * risk, 2)
    return {
        "ticker": ticker, "label": label,
        "entry_low": round(d["close"] * 0.9975, 2),
        "entry_high": round(d["close"] * 1.0025, 2),
        "stop": stop, "target_1": t1, "target_2": t2,
        "size_pct": round(size_pct, 2),
        "confidence": max(1, min(10, round(score / 10))),
        "thesis": ", ".join(reasons[:3]) or "trend and momentum",
        "invalidation": f"Daily close below {stop}",
        "rr1": round((t1 - d["close"]) / risk, 2),
        "rr2": round((t2 - d["close"]) / risk, 2),
        "score": round(score, 1),
    }


# ── format ────────────────────────────────────────────────────────────────────

def _fmt_ticket(t: dict, idx: int) -> str:
    tag = f" [{t['label']}]" if t["label"] != "portfolio" else ""
    return (
        f"TOP TRADE {idx}{tag}\n"
        f"- Buy {t['ticker']}\n"
        f"- Entry: {t['entry_low']:.2f} to {t['entry_high']:.2f}\n"
        f"- Stop: {t['stop']:.2f}\n"
        f"- Target: {t['target_1']:.2f} / {t['target_2']:.2f}\n"
        f"- Size: {t['size_pct']:.2f}% of portfolio\n"
        f"- Confidence: {t['confidence']}/10\n"
        f"- Thesis: {t['thesis']}\n"
        f"- Invalidation: {t['invalidation']}\n"
        f"- Risk/Reward: {t['rr1']:.2f} / {t['rr2']:.2f}"
    )


def build_report(tickets: list[dict], no_action: list[str], bench: list[str]) -> str:
    ts = datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d %H:%M %Z")
    report_page_url = os.getenv("REPORT_PAGE_URL", "").strip()
    lines = [
        f"── TRADE TICKETS | {ts} ──",
        f"Portfolio: ${PORTFOLIO_VALUE:,.0f} | Risk/trade: {RISK_PCT:.2f}% | Max size: {MAX_POS_PCT:.2f}%",
        "",
    ]
    if tickets:
        lines.append("NEW ORDERS")
        for i, t in enumerate(tickets, 1):
            lines.append(_fmt_ticket(t, i))
            lines.append("")
    else:
        lines.append("NEW ORDERS\n- No qualified setups today\n")

    lines.append("NO ACTION (portfolio)")
    for name in no_action[:8]:
        lines.append(f"- {name}")
    lines.append("")

    if bench:
        lines.append("WILDCARD BENCH")
        for name in bench[:5]:
            lines.append(f"- {name}")
        lines.append("")

    if report_page_url:
        lines.append(f"FULL REPORT: {report_page_url}")
        lines.append("")

    lines.append("Manual execution only. Use hard stops.")
    return "\n".join(lines).strip()


# ── telegram ──────────────────────────────────────────────────────────────────

def _send(text: str) -> None:
    bot = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat = os.getenv("TELEGRAM_CHAT_ID", "")
    if not bot or not chat:
        print("[trade_tickets] No Telegram credentials — printing only", file=sys.stderr)
        print(text)
        return
    url = f"https://api.telegram.org/bot{bot}/sendMessage"
    payload = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    req = urllib.request.Request(url, data=payload, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            resp = json.loads(r.read())
            if resp.get("ok"):
                print("[trade_tickets] Telegram message sent OK", file=sys.stderr)
            else:
                print(f"[trade_tickets] Telegram error: {resp}", file=sys.stderr)
    except Exception as exc:
        print(f"[trade_tickets] Telegram send failed: {exc}", file=sys.stderr)


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    portfolio = _csv("PORTFOLIO", DEFAULT_PORTFOLIO)
    wildcards = [t for t in _csv("WILDCARD_UNIVERSE", DEFAULT_WILDCARDS) if t not in set(portfolio)]

    all_tickets: list[dict] = []
    no_action: list[str] = []

    print("[trade_tickets] Fetching portfolio data...", file=sys.stderr)
    for ticker in portfolio:
        d = _fetch(ticker)
        if d is None:
            no_action.append(f"{ticker}: no data")
            continue
        score, reasons = _score(d)
        ok, reason = _qualifies(d)
        if not ok:
            no_action.append(f"{ticker}: {reason}")
            continue
        t = _ticket(ticker, d, score, reasons, "portfolio")
        if t["rr1"] >= MIN_RR:
            all_tickets.append(t)
        else:
            no_action.append(f"{ticker}: R/R below threshold")

    print("[trade_tickets] Prescanning wildcards...", file=sys.stderr)
    wc_scored: list[tuple[str, float, dict, list[str]]] = []
    for ticker in wildcards:
        d = _fetch(ticker)
        if d is None:
            continue
        score, reasons = _score(d)
        ok, _ = _qualifies(d)
        if ok:
            wc_scored.append((ticker, score, d, reasons))
    wc_scored.sort(key=lambda x: x[1], reverse=True)

    bench: list[str] = []
    promoted = 0
    for ticker, score, d, reasons in wc_scored:
        if len(all_tickets) >= MAX_ORDERS:
            bench.append(f"{ticker}: score {score:.0f}")
            continue
        if promoted < WILDCARD_COUNT:
            t = _ticket(ticker, d, score, reasons, "wildcard")
            if t["rr1"] >= MIN_RR:
                all_tickets.append(t)
                promoted += 1
            else:
                bench.append(f"{ticker}: R/R below threshold")
        else:
            bench.append(f"{ticker}: score {score:.0f}")

    all_tickets.sort(key=lambda x: (x["label"] != "portfolio", -x["score"]))
    report = build_report(all_tickets[:MAX_ORDERS], no_action, bench)
    reports_dir = Path(__file__).resolve().parent.parent / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    date_key = datetime.now(ZoneInfo("America/Chicago")).strftime("%Y%m%d")
    (reports_dir / f"trade_tickets_{date_key}.md").write_text(report, encoding="utf-8")
    _send(report)


if __name__ == "__main__":
    main()
