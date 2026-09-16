#!/bin/zsh
set -eu

RUNTIME_DIR="$HOME/Library/Application Support/PokemonStockMonitor"
PROJECT_DIR="$RUNTIME_DIR/app"
TOKEN_SERVICE="pokemon-stock-monitor.telegram-token"
CHAT_SERVICE="pokemon-stock-monitor.telegram-chat-id"

token="$(/usr/bin/security find-generic-password -a "$USER" -s "$TOKEN_SERVICE" -w 2>/dev/null || true)"
chat_id="$(/usr/bin/security find-generic-password -a "$USER" -s "$CHAT_SERVICE" -w 2>/dev/null || true)"

if [[ -z "$token" || -z "$chat_id" ]]; then
  print -u2 "Configuration Telegram absente du Trousseau macOS. Relancez scripts/setup_local_monitor.zsh."
  exit 2
fi

export TELEGRAM_BOT_TOKEN="$token"
export TELEGRAM_CHAT_ID="$chat_id"
mode="${1:---products-only}"
if [[ "$mode" == "--discovery-only" ]]; then
  export STATE_FILE="$RUNTIME_DIR/state-discovery.json"
else
  export STATE_FILE="$RUNTIME_DIR/state-products.json"
fi

cd "$PROJECT_DIR"
exec "$RUNTIME_DIR/venv/bin/python" "$PROJECT_DIR/monitor.py" "$mode"
