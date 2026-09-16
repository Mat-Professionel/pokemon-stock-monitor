#!/bin/zsh
set -eu

PROJECT_DIR="/Users/leo/Documents/New project/pokemon-stock-monitor"
RUNTIME_DIR="$HOME/Library/Application Support/PokemonStockMonitor"
LABEL="com.leo.pokemon-stock-monitor"
DISCOVERY_LABEL="com.leo.pokemon-stock-monitor.discovery"
HEALTH_LABEL="com.leo.pokemon-stock-monitor.health"
CONTROL_LABEL="com.leo.pokemon-stock-monitor.control"
CANARY_LABEL="com.leo.pokemon-stock-monitor.canaries"
PLIST_SOURCE="$PROJECT_DIR/launchd/$LABEL.plist"
DISCOVERY_PLIST_SOURCE="$PROJECT_DIR/launchd/$DISCOVERY_LABEL.plist"
HEALTH_PLIST_SOURCE="$PROJECT_DIR/launchd/$HEALTH_LABEL.plist"
CONTROL_PLIST_SOURCE="$PROJECT_DIR/launchd/$CONTROL_LABEL.plist"
CANARY_PLIST_SOURCE="$PROJECT_DIR/launchd/$CANARY_LABEL.plist"
PLIST_TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"
DISCOVERY_PLIST_TARGET="$HOME/Library/LaunchAgents/$DISCOVERY_LABEL.plist"
HEALTH_PLIST_TARGET="$HOME/Library/LaunchAgents/$HEALTH_LABEL.plist"
CONTROL_PLIST_TARGET="$HOME/Library/LaunchAgents/$CONTROL_LABEL.plist"
CANARY_PLIST_TARGET="$HOME/Library/LaunchAgents/$CANARY_LABEL.plist"
LOG_DIR="$HOME/Library/Logs/PokemonStockMonitor"
TOKEN_SERVICE="pokemon-stock-monitor.telegram-token"
CHAT_SERVICE="pokemon-stock-monitor.telegram-chat-id"
USER_ID="$(/usr/bin/id -u)"

token="$(/usr/bin/security find-generic-password -a "$USER" -s "$TOKEN_SERVICE" -w 2>/dev/null || true)"
token_valid=""
if [[ -n "$token" ]]; then
  token_valid="$(/usr/bin/curl -sS --max-time 15 "https://api.telegram.org/bot${token}/getMe" 2>/dev/null | "$PROJECT_DIR/.venv/bin/python" -c 'import json,sys; print("yes" if json.load(sys.stdin).get("ok") is True else "no")' 2>/dev/null || true)"
fi
if [[ "$token_valid" != "yes" ]]; then
  token=""
  for attempt in 1 2 3; do
    token="$(/usr/bin/osascript -e 'text returned of (display dialog "Colle uniquement le token de @BotFather (exemple : 123456789:AA...) :" default answer "" with hidden answer buttons {"Annuler", "Vérifier"} default button "Vérifier" with title "Moniteur Pokémon")')"
    token="$(print -rn -- "$token" | /usr/bin/tr -d '[:space:]')"
    if [[ -z "$token" ]]; then
      print -u2 "Token vide, installation annulée."
      exit 2
    fi
    token_valid="$(/usr/bin/curl -sS --max-time 15 "https://api.telegram.org/bot${token}/getMe" 2>/dev/null | "$PROJECT_DIR/.venv/bin/python" -c 'import json,sys; print("yes" if json.load(sys.stdin).get("ok") is True else "no")' 2>/dev/null || true)"
    if [[ "$token_valid" == "yes" ]]; then
      break
    fi
    token=""
    /usr/bin/osascript -e 'display alert "Token invalide" message "Copie uniquement la valeur envoyée par BotFather après “Use this token to access the HTTP API”." as warning' >/dev/null
  done
fi
if [[ -z "$token" ]]; then
  print -u2 "Token invalide après trois essais, installation annulée."
  exit 2
fi

chat_id="$(/usr/bin/security find-generic-password -a "$USER" -s "$CHAT_SERVICE" -w 2>/dev/null || true)"
if [[ -z "$chat_id" ]]; then
  chat_id="$(/usr/bin/curl -fsS --max-time 15 "https://api.telegram.org/bot${token}/getUpdates" | "$PROJECT_DIR/.venv/bin/python" -c 'import json,sys; p=json.load(sys.stdin); ids=[str(x.get("message",{}).get("chat",{}).get("id", "")) for x in p.get("result",[])]; print(next((x for x in reversed(ids) if x), ""))' 2>/dev/null || true)"
fi
if [[ -z "$chat_id" ]]; then
  chat_id="$(/usr/bin/osascript -e 'text returned of (display dialog "Impossible de retrouver automatiquement le chat Telegram. Colle le TELEGRAM_CHAT_ID :" default answer "" buttons {"Annuler", "Enregistrer"} default button "Enregistrer" with title "Moniteur Pokémon")')"
fi
if [[ -z "$chat_id" ]]; then
  print -u2 "Chat ID vide, installation annulée."
  exit 2
fi

/usr/bin/security add-generic-password -U -a "$USER" -s "$TOKEN_SERVICE" -w "$token" >/dev/null
/usr/bin/security add-generic-password -U -a "$USER" -s "$CHAT_SERVICE" -w "$chat_id" >/dev/null
unset token chat_id

/bin/mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR" "$RUNTIME_DIR/app"
/usr/bin/rsync -a \
  --exclude '.git/' \
  --exclude '.venv/' \
  --exclude '.local/' \
  --exclude '__pycache__/' \
  "$PROJECT_DIR/" "$RUNTIME_DIR/app/"
/bin/cp "$PROJECT_DIR/scripts/run_local_monitor.zsh" "$RUNTIME_DIR/run_local_monitor.zsh"
if [[ ! -x "$RUNTIME_DIR/venv/bin/python" ]]; then
  "$PROJECT_DIR/.venv/bin/python" -m venv "$RUNTIME_DIR/venv"
fi
"$RUNTIME_DIR/venv/bin/python" -m pip install -q -r "$RUNTIME_DIR/app/requirements.txt"
"$RUNTIME_DIR/venv/bin/python" -m playwright install chromium
/bin/cp "$PLIST_SOURCE" "$PLIST_TARGET"
/bin/cp "$DISCOVERY_PLIST_SOURCE" "$DISCOVERY_PLIST_TARGET"
/bin/cp "$HEALTH_PLIST_SOURCE" "$HEALTH_PLIST_TARGET"
/bin/cp "$CONTROL_PLIST_SOURCE" "$CONTROL_PLIST_TARGET"
/bin/cp "$CANARY_PLIST_SOURCE" "$CANARY_PLIST_TARGET"
/bin/chmod 600 "$PLIST_TARGET" "$DISCOVERY_PLIST_TARGET" "$HEALTH_PLIST_TARGET" "$CONTROL_PLIST_TARGET" "$CANARY_PLIST_TARGET"
/bin/chmod 700 "$RUNTIME_DIR/run_local_monitor.zsh"

/bin/launchctl bootout "gui/$USER_ID/$LABEL" >/dev/null 2>&1 || true
/bin/launchctl bootout "gui/$USER_ID/$DISCOVERY_LABEL" >/dev/null 2>&1 || true
/bin/launchctl bootout "gui/$USER_ID/$HEALTH_LABEL" >/dev/null 2>&1 || true
/bin/launchctl bootout "gui/$USER_ID/$CONTROL_LABEL" >/dev/null 2>&1 || true
/bin/launchctl bootout "gui/$USER_ID/$CANARY_LABEL" >/dev/null 2>&1 || true
/bin/sleep 1
/bin/launchctl bootstrap "gui/$USER_ID" "$PLIST_TARGET"
/bin/launchctl bootstrap "gui/$USER_ID" "$DISCOVERY_PLIST_TARGET"
/bin/launchctl bootstrap "gui/$USER_ID" "$HEALTH_PLIST_TARGET"
/bin/launchctl bootstrap "gui/$USER_ID" "$CONTROL_PLIST_TARGET"
/bin/launchctl bootstrap "gui/$USER_ID" "$CANARY_PLIST_TARGET"
/bin/launchctl kickstart -k "gui/$USER_ID/$LABEL"
/bin/launchctl kickstart -k "gui/$USER_ID/$DISCOVERY_LABEL"
/bin/launchctl kickstart -k "gui/$USER_ID/$CONTROL_LABEL"
/bin/launchctl kickstart -k "gui/$USER_ID/$CANARY_LABEL"

print "Moniteur local installé : stock toutes les 60 secondes, découverte, témoins et rapport de santé toutes les 10 minutes."
print "Logs : $LOG_DIR"
