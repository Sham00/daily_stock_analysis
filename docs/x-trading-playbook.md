# X API Trading Playbook

## Goal
Use X as a low-cost, high-signal context layer for trading, not a firehose.

## Auth model
Use **app-only OAuth 2.0 bearer token** for read-only recent search.
This is the simplest and cheapest way to pull public market chatter for trading context.

## Recommended low-budget setup
- Use `X_API_BEARER_TOKEN`
- Query only:
  - macro tape once per run
  - top 4 portfolio names
- Use recent search only, not streaming
- Keep `X_API_MAX_RESULTS` low (8 to 10)
- Run only on the existing morning and afternoon jobs

## Why this stays cheap
- X API is pay-per-use with credits
- docs say billable resources are typically deduplicated within a 24-hour UTC window
- recent search queries are enough for market context without paying for enterprise/streaming scale

## Best uses for trading
### 1. Macro tape check
Look for regime shifts:
- FOMC
- CPI / PPI / NFP
- yields / treasury
- tariffs
- semis
- bitcoin

### 2. Portfolio pulse
For held names, scan for:
- earnings
- guidance
- upgrades / downgrades
- SEC / filings
- production updates
- deal chatter

### 3. Event confirmation
Use X to confirm whether a move has real narrative fuel or is just price action.

### 4. Risk flags
Use it to catch sudden negative narrative bursts before they fully propagate into slower news summaries.

## Not recommended for low budget
- filtered stream
- enterprise firehose
- broad user lookups at scale
- scanning dozens of tickers every run

## Workflow integration
The GitHub workflow now supports an optional `scripts/x_market_intel.py` step.
If `X_API_BEARER_TOKEN` is set, it writes `reports/x_market_intel_YYYYMMDD.md`.
That report is included in the static daily page.

## Suggested first config
- `X_API_PORTFOLIO_LIMIT=4`
- `X_API_MAX_RESULTS=10`
- `X_API_MACRO_QUERY=(FOMC OR CPI OR PPI OR NFP OR yields OR treasury OR tariffs OR semis OR bitcoin) lang:en -is:retweet`

## Next upgrades
- add engagement threshold filtering
- add author allowlist / denylist
- rank posts by likes + reposts + keyword severity
- cache per query per UTC day
- thread important X findings into the Telegram summary header
