#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

python3 -m venv .venv
"$ROOT_DIR/.venv/bin/python" -m pip install --upgrade pip
"$ROOT_DIR/.venv/bin/python" -m pip install -r requirements.txt

if [ ! -f .env ] && [ -f .env.example ]; then
  cp .env.example .env
  echo ".env created from .env.example"
fi

cat <<'EOF'
Setup complete.

Activate the virtualenv:
  source .venv/bin/activate

Run Telegram bot:
  ./bin/run_telegram_bot.sh

Run MCP server:
  ./bin/run_mcp.sh
EOF
