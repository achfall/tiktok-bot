# Instruksi buat Hermes — TikTok Bot

## Manajemen Produk

Kalau Falla minta nambah/hapus/toggle produk (via WA, voice note, atau chat
kapan aja saat dia gak di depan mesin), **PAKAI CLI YANG UDAH ADA INI**.
JANGAN bikin script baru, JANGAN edit database SQLite manual, JANGAN edit
CSV — CSV udah gak dipakai lagi, source of truth-nya langsung di database
lewat CLI ini.

Lokasi: `~/tiktok-bot/scripts/manage_products.py`

```bash
# Lihat semua produk + statusnya (sort_order, active ON/off, product_id, folder_name)
python3 ~/tiktok-bot/scripts/manage_products.py list akun1

# Tambah produk baru
# -- Syarat: folder video-nya HARUS udah ada isinya dulu di
#    ~/tiktok-bot/content/<akun>/<nama_folder>/ (isi .mp4/.mov) SEBELUM run ini,
#    soalnya command ini otomatis scan folder itu buat masukin video ke queue.
python3 ~/tiktok-bot/scripts/manage_products.py add akun1 "Nama Produk Baru" 1234567890123456789

# Matiin produk (stok habis, dll) -- ini SATU-SATUNYA yang perlu dilakukan
# buat "hapus" produk dari rotasi. Gak perlu hapus folder/row/apapun.
python3 ~/tiktok-bot/scripts/manage_products.py disable akun1 "Nama Produk Lama"

# Aktifin lagi produk yang sempat di-disable
python3 ~/tiktok-bot/scripts/manage_products.py enable akun1 "Nama Produk Lama"

# Ganti/perbaiki product ID (misal salah ketik pas nambah)
python3 ~/tiktok-bot/scripts/manage_products.py set-id akun1 "Nama Produk" 9999999999999999999
```

Catatan: `"Nama Produk"` harus PERSIS SAMA dengan nama folder (case-sensitive,
termasuk spasi/tanda baca). Toggle disable/enable murni lokal ke database,
gak ada ketergantungan ke showcase TikTok (bot pilih produk lewat
search-by-product-ID, bukan posisi di showcase).

## Menjalankan / Mengecek Status Bot (PENTING: BUKAN TMUX LAGI)

Per 20 Sep 2026, bot akun1/2/3 dikelola pakai **systemd user service**,
bukan tmux lagi. JANGAN jalanin `daily_orchestrator.py` manual di
tmux/terminal — udah otomatis jalan sebagai service, auto-restart kalau
gagal, dan auto-start pas mesin boot.

```bash
# Cek status semua akun sekaligus
systemctl --user status tiktokbot-akun1 tiktokbot-akun2 tiktokbot-akun3 --no-pager

# Liat progress/log terakhir
tail -30 ~/tiktok-bot/logs/akun1-service.log
tail -30 ~/tiktok-bot/logs/akun2-service.log
tail -30 ~/tiktok-bot/logs/akun3-service.log

# Mantau log real-time (Ctrl+C buat berenti mantau, service TETAP jalan)
tail -f ~/tiktok-bot/logs/akun1-service.log

# Cek history publish hari ini dari database (paling akurat)
sqlite3 -header -column ~/tiktok-bot/db/tiktok_bot.db "SELECT a.slug, p.folder_name, pl.status, pl.published_at FROM posts_log pl JOIN accounts a ON pl.account_id=a.id JOIN products p ON pl.product_id=p.id WHERE DATE(pl.published_at)=DATE('now') ORDER BY pl.published_at DESC LIMIT 20;"

# Restart 1 akun (misal abis ada patch baru, atau macet)
systemctl --user restart tiktokbot-akun1

# Matiin / nyalain sementara
systemctl --user stop tiktokbot-akun1
systemctl --user start tiktokbot-akun1
```

Service file-nya ada di `~/.config/systemd/user/tiktokbot-akun{1,2,3}.service`.
Kalau nambah akun baru (akun4 dst), copy pola file yang udah ada, ganti nomor
akun dan port CDP-nya (9221=akun1, 9222=akun2, 9223=akun3, 9224=akun4, dst),
terus `systemctl --user daemon-reload && systemctl --user enable --now tiktokbot-akun4`.

Kalau ada kebutuhan yang gak ke-cover di atas, tanya Falla dulu sebelum
bikin solusi baru — jangan asumsi sendiri.

## Auto-Restart Harian (Timer)

Selain auto-restart pas mesin boot, ada juga **systemd timer** yang otomatis
restart tiap service jam 10.00 pagi tiap hari — ini nutup celah kalau
service-nya berhenti di tengah hari (misal kena "STOP TOTAL" gara-gara
error) dan gak jalan lagi sendiri sampai besok.

```bash
# Cek jadwal timer
systemctl --user list-timers --no-pager

# File timer ada di:
~/.config/systemd/user/tiktokbot-akun{1,2,3}.timer
```

Kalau ada akun baru (akun4 dst), timer-nya juga perlu dibikin manual
(copy pola file timer yang udah ada), gak otomatis ke-generate.
