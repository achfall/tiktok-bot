#!/usr/bin/env bash
set -euo pipefail

PROFILE_NAME="${1:?Usage: $0 <nama_profile> <cdp_port>}"
CDP_PORT="${2:?Usage: $0 <nama_profile> <cdp_port>}"

BOT_ROOT="${TIKTOK_BOT_HOME:-$HOME/tiktok-bot}"
PROFILE_DIR="$BOT_ROOT/chrome-profiles/$PROFILE_NAME"
LOG_FILE="$BOT_ROOT/logs/${PROFILE_NAME}.log"

[[ "$PROFILE_NAME" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]] || { echo "Nama profil tidak valid" >&2; exit 1; }
[[ "$CDP_PORT" =~ ^[0-9]+$ ]] && (( CDP_PORT >= 1024 && CDP_PORT <= 65535 )) || { echo "Port tidak valid" >&2; exit 1; }

# DB menyimpan lokasi profil; ID akun dan nama folder dapat berbeda.
RESOLVER="$(dirname "$0")/chrome_profile_path.py"
[[ -f "$RESOLVER" ]] || { echo "Resolver profil tidak ditemukan" >&2; exit 1; }
PYTHON_BIN="${TIKTOK_BOT_PYTHON:-$BOT_ROOT/venv/bin/python}"
[[ -x "$PYTHON_BIN" ]] || PYTHON_BIN="$(command -v python3)"
PROFILE_DIR="$("$PYTHON_BIN" "$RESOLVER" "$PROFILE_NAME" "$CDP_PORT")"
PROFILE_DIRECTORY="$("$PYTHON_BIN" "$RESOLVER" "$PROFILE_NAME" "$CDP_PORT" --directory)"

if [[ "$(uname -s)" == Darwin ]]; then
  CHROME_BIN="${CHROME_BIN:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
else
  CHROME_BIN="${CHROME_BIN:-$(command -v google-chrome || command -v chromium || command -v chromium-browser || true)}"
fi
[[ -x "$CHROME_BIN" ]] || { echo "Chrome tidak ditemukan: $CHROME_BIN" >&2; exit 1; }
mkdir -p "$PROFILE_DIR" "$(dirname "$LOG_FILE")"

CHROME_ARGS=(
  --user-data-dir="$PROFILE_DIR"
  --profile-directory="$PROFILE_DIRECTORY"
  --remote-debugging-port="$CDP_PORT"
  --remote-debugging-address=127.0.0.1
  --remote-allow-origins=http://127.0.0.1:$CDP_PORT
  --no-first-run
  --no-default-browser-check
  --disable-session-crashed-bubble
)

if [[ "$(uname -s)" == Darwin && "$CHROME_BIN" == *.app/Contents/MacOS/* ]]; then
  # LaunchServices membuka Chrome dalam sesi GUI macOS agar akses Keychain
  # tidak mewarisi konteks proses panel/agent yang menjalankannya.
  CHROME_APP="${CHROME_BIN%/Contents/MacOS/*}"
  /usr/bin/open -n -a "$CHROME_APP" --args "${CHROME_ARGS[@]}"
  echo "Chrome profile '$PROFILE_NAME' diminta dibuka melalui macOS."
else
  "$CHROME_BIN" "${CHROME_ARGS[@]}" > "$LOG_FILE" 2>&1 &
  echo "Chrome profile '$PROFILE_NAME' started (PID $!)"
fi
echo "Profile folder: $PROFILE_DIR/$PROFILE_DIRECTORY"
echo "CDP endpoint: http://127.0.0.1:$CDP_PORT"
