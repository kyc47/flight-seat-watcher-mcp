#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

uv sync

if [ ! -f .env ] && [ -f .env.example ]; then
  cp .env.example .env
  echo ".env created from .env.example"
fi

cat <<'EOF'
Setup complete.

Run Telegram bot:
  ./bin/run_telegram_bot.sh

Run MCP server:
  ./bin/run_mcp.sh
EOF
