#!/usr/bin/env bash
set -euo pipefail

PROFILE_NAME="${1:?Usage: $0 <nama_profile> <cdp_port>}"
CDP_PORT="${2:?Usage: $0 <nama_profile> <cdp_port>}"

PROFILE_DIR="$HOME/tiktok-bot/chrome-profiles/$PROFILE_NAME"
LOG_FILE="$HOME/tiktok-bot/logs/${PROFILE_NAME}.log"

mkdir -p "$PROFILE_DIR"

google-chrome \
  --user-data-dir="$PROFILE_DIR" \
  --remote-debugging-port="$CDP_PORT" \
  --remote-debugging-address=127.0.0.1 \
  --remote-allow-origins=http://127.0.0.1:$CDP_PORT \
  --no-first-run \
  --no-default-browser-check \
  --disable-session-crashed-bubble \
  > "$LOG_FILE" 2>&1 &

echo "Chrome profile '$PROFILE_NAME' started (PID $!)"
echo "CDP endpoint: http://127.0.0.1:$CDP_PORT"
