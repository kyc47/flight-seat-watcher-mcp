# AGENTS.md

## Project Summary

This repository provides a configurable flight watcher with two primary entry points:

1. `scripts/telegram_bot_runner.py`
   Telegram bot for end users. Stores per-chat watch configurations and sends alerts.
2. `scripts/flight_watch_mcp.py`
   `stdio` MCP server for AI clients and local MCP-enabled tools.

The actual watch/query logic is centralized in:

- `scripts/flight_watch_dynamic.py`

Legacy single-run helpers still exist in:

- `scripts/naver_flight_watch.py`

## Main Features

- Up to 5 watches per Telegram chat
- Domestic and international routes
- One-way and round-trip watches
- Departure and return time-range filtering
- Telegram polling loop
- MCP tools for programmatic access

## Important Files

- `README.md`: human-facing project documentation
- `pyproject.toml`: uv project metadata and dependencies
- `bin/setup_local.sh`: local setup helper
- `bin/run_telegram_bot.sh`: start Telegram bot
- `bin/run_mcp.sh`: start MCP server
- `.env.example`: environment variable template
- `.mcp.json.example`: sample MCP client configuration

## Environment Variables

Supported environment variables:

- `TELEGRAM_BOT_TOKEN`: preferred Telegram bot token variable
- `TELEGRAMBOT`: legacy Telegram bot token variable
- `TELEGRAM_CHAT_ID`: optional Telegram chat id for testing
- `CHROME_BINARY`: optional explicit Chrome binary path
- `GOOGLE_CHROME_BIN`: optional alternate Chrome binary path

## Local Setup

Recommended setup:

```bash
./bin/setup_local.sh
```

## Runtime Commands

Run Telegram bot:

```bash
./bin/run_telegram_bot.sh
```

Run MCP server:

```bash
./bin/run_mcp.sh
```

Manual single-run check:

```bash
uv run python scripts/naver_flight_watch.py
```

## MCP Tools

Current MCP server tools exposed by `scripts/flight_watch_mcp.py`:

- `check_flights`
- `get_manual_summary`
- `send_telegram_test`

`check_flights` and `get_manual_summary` accept a `watches` array. Each watch should include:

- `origin`
- `destination`
- `market`: `domestic` or `international`
- `trip_type`: `oneway` or `roundtrip`
- `departure_date`: `YYYYMMDD`
- `departure_time_range`: `HH:MM~HH:MM`

Optional watch fields:

- `return_date`
- `return_time_range`
- `adults`

## Telegram Conversation Flow

When a user starts a new watch in Telegram:

1. origin airport
2. destination airport
3. trip type button
4. departure date
5. return date if round-trip
6. departure time range
7. return time range if round-trip

## Constraints and Notes

- `airport.co.kr` is only used for domestic routes
- International route checks currently rely on Naver result pages
- Selenium and Chrome are required for actual flight scraping
- This repository currently provides a local `stdio` MCP server, not a remote HTTP/SSE MCP server

## Safe Assumptions for AI Agents

- Use the `bin/` scripts instead of hardcoding Python paths
- Prefer `uv run` and `uv sync` for execution and dependency setup
- Prefer `TELEGRAM_BOT_TOKEN` over `TELEGRAMBOT`
- Do not commit `.env` or `.state`
- If Chrome is not auto-detected, instruct the user to set `CHROME_BINARY`
