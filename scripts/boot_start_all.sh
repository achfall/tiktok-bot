#!/usr/bin/env bash
# boot_start_all.sh
# Dijalankan otomatis saat mesin boot (lewat cron @reboot).
# Nyalain Chrome + tmux + orchestrator buat akun1-3 (akun4 belum ada
# produk, gampang ditambahin belakangan tinggal copy pola di bawah).

LOG_FILE="$HOME/tiktok-bot/logs/boot_start.log"
mkdir -p "$HOME/tiktok-bot/logs"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" >> "$LOG_FILE"
}

log "=== Boot start script dimulai ==="

# Tunggu desktop XFCE beneran siap dulu (auto-login butuh waktu sebelum X11 fully up)
log "Tunggu 90 detik buat desktop siap..."
sleep 90

export DISPLAY=:0

# Tunggu CDP port merespon (polling, bukan blind sleep -- lebih pasti)
wait_for_cdp() {
    local port=$1
    local max_wait=30
    local waited=0
    while [ $waited -lt $max_wait ]; do
        if curl -s "http://127.0.0.1:${port}/json/version" > /dev/null 2>&1; then
            return 0
        fi
        sleep 2
        waited=$((waited + 2))
    done
    return 1
}

start_account() {
    local account=$1
    local port=$2
    local session="tiktokbot-${account}"

    log "Nyalain Chrome $account (port $port)..."
    "$HOME/tiktok-bot/scripts/start-chrome-profile.sh" "$account" "$port" >> "$LOG_FILE" 2>&1

    if wait_for_cdp "$port"; then
        log "Chrome $account siap (CDP port $port merespon)."
    else
        log "WARNING: Chrome $account gak merespon di port $port setelah 30 detik, tetep lanjut."
    fi

    log "Bikin tmux session $session dan jalanin orchestrator..."
    tmux new-session -d -s "$session" \
        "cd $HOME/tiktok-bot && source venv/bin/activate && python3 scripts/daily_orchestrator.py $account --publish 2>&1 | tee -a $LOG_FILE"
    log "$account started (session: $session)."
}

start_account akun1 9221
sleep 10
start_account akun2 9222
sleep 10
start_account akun3 9223

log "=== Semua akun udah di-start ==="
