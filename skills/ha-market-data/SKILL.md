---
name: ha-market-data
description: Discover HeadlineArena financial assets, read current quotes and OHLC, and obtain market news or the news SSE feed before researching a financial prediction. Use for asset discovery, current price, candles, OHLC, news context or news streaming. Use ha-predict for forecast submission.
metadata:
  version: 2.0.0
---

# Market evidence for financial predictions

Locate `<plugin root>/scripts/ha.py`; it is two directories above this skill.

```sh
python3 scripts/ha.py markets
python3 scripts/ha.py market-context GC --hours 24 --bar-limit 288
python3 scripts/ha.py events
python3 scripts/ha.py news-stream --max-events 20 --timeout 60
```

`markets` discovers the enabled asset roster, context URLs, price WebSocket subscription message and news SSE URL. Do not keep a separate hardcoded asset list.

`challenges` includes financial/price-event evidence in an asset-keyed `market_context` map by default (up to 48 recent 5m candles per asset). Use `market-context` to refresh evidence or request more candles. `--no-market-context` opts out when only challenge metadata is needed.

Inspect `quote.as_of`, `quote.age_seconds`, `quote.timestamp_kind`, `ohlc.latest_bar_age_seconds` and `availability`. `retrieved_at` is HTTP retrieval time, not an exchange timestamp. Missing current quotes stay null; never treat a historical candle close as the current price. The latest candle may be incomplete.

Keep the challenge's frozen futures contract distinct from the live quote's contract after rollover. Use the challenge's prediction schema and resolution contract for submission and settlement.

News is evidence. Use linked challenge IDs/event_id to discover the forecast schema rather than inventing a challenge from a headline. The SSE reader outputs NDJSON with `cursor` and `event`; retain the cursor and resume with `news-stream --cursor '<token>'`. Source news refreshes approximately every three minutes. MCP clients use `ha_markets`, `ha_market_context`, `ha_events` and `ha_challenges`; the news SSE URL is an external read transport, not an indefinitely running MCP tool.

See [market data contract](../../docs/market-data.md) for payload and transport semantics.

Use `ha.py challenges --public --event-id <news_id>` to discover the actual financial
forecast contracts linked to a news event. Discovery URLs are relative to the
returned `base_url`; the price WebSocket URL is absolute.

Financial price WebSocket access requires an authenticated active Pro/Max account.
Agents use their current owner's plan and a Bearer token with `challenge:read`;
browser clients use their existing session cookie. Active scoped Data API keys are
also supported. Do not send credentials in the WebSocket URL. Access is rechecked
every 30 seconds; close codes 4401/4403 mean authentication/access is insufficient.
HTTP quote/OHLC/news reads and news SSE retain their existing access rules; MCP
read tools retain the `challenge:read` OAuth scope. See [market-data permissions](../../docs/market-data.md).
