#!/bin/zsh
set -eu

LABEL="com.leo.pokemon-stock-monitor"
DISCOVERY_LABEL="com.leo.pokemon-stock-monitor.discovery"
HEALTH_LABEL="com.leo.pokemon-stock-monitor.health"
CONTROL_LABEL="com.leo.pokemon-stock-monitor.control"
PLIST_TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"
DISCOVERY_PLIST_TARGET="$HOME/Library/LaunchAgents/$DISCOVERY_LABEL.plist"
HEALTH_PLIST_TARGET="$HOME/Library/LaunchAgents/$HEALTH_LABEL.plist"
CONTROL_PLIST_TARGET="$HOME/Library/LaunchAgents/$CONTROL_LABEL.plist"
USER_ID="$(/usr/bin/id -u)"

/bin/launchctl bootout "gui/$USER_ID/$LABEL" >/dev/null 2>&1 || true
/bin/launchctl bootout "gui/$USER_ID/$DISCOVERY_LABEL" >/dev/null 2>&1 || true
/bin/launchctl bootout "gui/$USER_ID/$HEALTH_LABEL" >/dev/null 2>&1 || true
/bin/launchctl bootout "gui/$USER_ID/$CONTROL_LABEL" >/dev/null 2>&1 || true
if [[ -f "$PLIST_TARGET" ]]; then
  /bin/mv "$PLIST_TARGET" "$HOME/.Trash/$LABEL.plist"
fi
if [[ -f "$DISCOVERY_PLIST_TARGET" ]]; then
  /bin/mv "$DISCOVERY_PLIST_TARGET" "$HOME/.Trash/$DISCOVERY_LABEL.plist"
fi
if [[ -f "$HEALTH_PLIST_TARGET" ]]; then
  /bin/mv "$HEALTH_PLIST_TARGET" "$HOME/.Trash/$HEALTH_LABEL.plist"
fi
if [[ -f "$CONTROL_PLIST_TARGET" ]]; then
  /bin/mv "$CONTROL_PLIST_TARGET" "$HOME/.Trash/$CONTROL_LABEL.plist"
fi
print "Moniteur local arrêté. Le fichier LaunchAgent a été placé dans la Corbeille."
print "Les identifiants restent dans le Trousseau macOS pour permettre une réinstallation."
