#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"
BASE_URL = "https://api.x.com/2/tweets/search/recent"
TIMEOUT = 20
DEFAULT_PORTFOLIO = "FANG,CNQ"
DEFAULT_MACRO_QUERY = '(FOMC OR CPI OR PPI OR NFP OR yields OR treasury OR tariffs OR semis OR bitcoin) lang:en -is:retweet'


@dataclass
class QueryJob:
    label: str
    query: str
    max_results: int = 10


def _csv(value: str) -> list[str]:
    return [item.strip().upper() for item in value.split(",") if item.strip()]


def _build_headers() -> dict[str, str]:
    token = (os.getenv("X_API_BEARER_TOKEN") or "").strip()
    if not token:
        raise SystemExit("X_API_BEARER_TOKEN is not set")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "daily-stock-analysis-x-intel/1.0",
    }


def _ticker_query(ticker: str) -> str:
    base = ticker.upper()
    cashtag = f"${base.replace('-USD', '')}"
    terms = [cashtag, f'"{base}"']
    if base == "B":
        terms.append('"Barrick"')
    if base == "BTC-USD":
        terms = ["bitcoin", "$BTC", "BTC"]
    elif base == "AGMRF":
        terms.append('"Silver Tiger"')
    elif base == "CSCCF":
        terms.append('"Capstone Copper"')

    query = f"({' OR '.join(terms)}) (earnings OR guidance OR upgrade OR downgrade OR filing OR SEC OR production OR capex OR deal) lang:en -is:retweet"
    return query


def _fetch(query: str, max_results: int) -> dict[str, Any]:
    headers = _build_headers()
    params = {
        "query": query,
        "max_results": str(max(10, min(max_results, 100))),
        "tweet.fields": "created_at,public_metrics,lang,author_id",
        "expansions": "author_id",
        "user.fields": "username,name,verified",
    }
    resp = requests.get(BASE_URL, headers=headers, params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _format_result(job: QueryJob, payload: dict[str, Any]) -> str:
    data = payload.get("data", []) or []
    users = {u.get("id"): u for u in (payload.get("includes", {}) or {}).get("users", [])}
    lines = [f"## {job.label}", f"Query: `{job.query}`", ""]
    if not data:
        lines.append("- No matching recent posts")
        lines.append("")
        return "\n".join(lines)

    for post in data[: job.max_results]:
        user = users.get(post.get("author_id"), {})
        username = user.get("username", "unknown")
        metrics = post.get("public_metrics", {}) or {}
        likes = metrics.get("like_count", 0)
        reposts = metrics.get("retweet_count", 0)
        replies = metrics.get("reply_count", 0)
        created_at = post.get("created_at", "")
        text = re.sub(r"\s+", " ", (post.get("text") or "").strip())
        if len(text) > 220:
            text = text[:217] + "..."
        lines.append(f"- @{username} | {created_at} | ❤ {likes} | 🔁 {reposts} | 💬 {replies}")
        lines.append(f"  {text}")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    portfolio = _csv(os.getenv("PORTFOLIO", DEFAULT_PORTFOLIO))
    macro_query = os.getenv("X_API_MACRO_QUERY", DEFAULT_MACRO_QUERY).strip()
    max_tickers = int(os.getenv("X_API_PORTFOLIO_LIMIT", "4"))
    max_results = int(os.getenv("X_API_MAX_RESULTS", "10"))

    jobs: list[QueryJob] = [QueryJob(label="Macro tape", query=macro_query, max_results=8)]
    for ticker in portfolio[:max_tickers]:
        jobs.append(QueryJob(label=f"{ticker} social/news pulse", query=_ticker_query(ticker), max_results=max_results))

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    date_key = datetime.utcnow().strftime("%Y%m%d")
    out_path = REPORTS_DIR / f"x_market_intel_{date_key}.md"

    lines = [
        f"# X Market Intel — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "Low-cost recent-search sweep using X app-only bearer token.",
        "Designed for portfolio-first trading context, not firehose monitoring.",
        "",
    ]

    for job in jobs:
        try:
            payload = _fetch(job.query, job.max_results)
            lines.append(_format_result(job, payload))
        except Exception as exc:
            lines.append(f"## {job.label}")
            lines.append(f"- Fetch failed: {exc}")
            lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(str(out_path))


if __name__ == "__main__":
    main()
