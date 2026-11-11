#!/usr/bin/env bash
# Hanya dipakai setelah operator sengaja mengaktifkan konfigurasi launchd.
set -euo pipefail
BOT_ROOT="${TIKTOK_BOT_HOME:-$HOME/tiktok-bot}"
CODE_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$BOT_ROOT"
account="${1:?account required}"
port="${2:?port required}"
[[ "$account" =~ ^akun[1-9][0-9]*$ ]] || { echo "Akun tidak valid" >&2; exit 1; }
[[ "$port" =~ ^[0-9]+$ ]] && (( port >= 1024 && port <= 65535 )) || { echo "Port tidak valid" >&2; exit 1; }
# Guard lokal; generator tidak pernah membuat file persetujuan ini.
[[ -f "config/allow-posting-$account" ]] || { echo "Posting belum diaktifkan untuk $account" >&2; exit 1; }
export TIKTOK_BOT_HOME="$BOT_ROOT"
PYTHON_BIN="${TIKTOK_BOT_PYTHON:-$CODE_ROOT/venv/bin/python}"
# Orchestrator memeriksa antrean/jam/slot sebelum membuka Chrome.
exec "$PYTHON_BIN" -u "$CODE_ROOT/scripts/daily_orchestrator.py" "$account" --publish
