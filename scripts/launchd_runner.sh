#!/usr/bin/env bash
# Hanya dipakai setelah operator sengaja mengaktifkan konfigurasi launchd.
set -euo pipefail
cd "$HOME/tiktok-bot"
account="${1:?account required}"
port="${2:?port required}"
# Guard lokal; generator tidak pernah membuat file persetujuan ini.
[[ -f "config/allow-posting-$account" ]] || { echo "Posting belum diaktifkan untuk $account" >&2; exit 1; }
bash scripts/start-chrome-profile.sh "$account" "$port"
for ((attempt=0; attempt<30; attempt++)); do
  if curl --fail --silent --max-time 2 "http://127.0.0.1:$port/json/version" >/dev/null; then
    exec venv/bin/python -u scripts/daily_orchestrator.py "$account" --publish
  fi
  sleep 2
done
echo "Chrome CDP tidak siap" >&2
exit 1
