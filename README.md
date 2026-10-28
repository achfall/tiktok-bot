# TikTok Bot

Bot upload video affiliate multi-akun melalui Chrome DevTools Protocol.
Caption menggunakan Gemini dengan fallback DeepSeek. Detail operasional ada
pada HERMES_INSTRUCTIONS.md dan arsitektur pada CLAUDE.md.

## Pindah device

Clone ke `~/tiktok-bot`; script produksi menggunakan lokasi ini.

```bash
cd ~/tiktok-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
mkdir -p logs db content chrome-profiles
```

Isi API key di `.env` secara lokal. File rahasia, database, video,
profil Chrome, dan konfigurasi produk lokal tidak disimpan di Git.
Transfer database SQLite yang konsisten beserta video melalui saluran pribadi
untuk mempertahankan akun, produk, antrean, serta riwayat posting.
Path video dalam database mungkin perlu disesuaikan dengan home device baru.
Jangan menjalankan bot akun yang sama pada dua device bersamaan.

## Catatan macOS

Install Python 3 dan Google Chrome. Launcher saat ini menggunakan executable
Linux `google-chrome` dan perlu disesuaikan ke
`/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`.
Login ulang tiap akun dengan profil Chrome khusus. Penjadwalan Linux memakai
systemd; macOS membutuhkan konfigurasi launchd terpisah. Beberapa script
pengujian lama juga memakai path `/home/sintia`.

Clone saja belum menghasilkan instalasi macOS siap publish. Selesaikan
pemindahan data, konfigurasi browser dan jadwal, lalu verifikasi alur
sebelum mengaktifkan auto-publish. Script tanpa `--publish` tetap berinteraksi
dengan browser dan mengunggah video untuk pengujian.
