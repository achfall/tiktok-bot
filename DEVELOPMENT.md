# Arsitektur panel lokal

Panel memakai Python standard library HTTP server dan HTML/CSS/JavaScript tanpa
build/dependensi frontend. Tujuannya mudah dijalankan di Mac Intel dan membatasi
perubahan pada alur bot yang sudah teruji.

## Batas tanggung jawab

| Modul | Tanggung jawab |
|---|---|
| `scripts/content_queue.py` | Scan, klaim atomik, state submit, pemulihan |
| `scripts/daily_orchestrator.py` | Interaksi TikTok dan verifikasi hasil |
| `scripts/manage_*.py`, `setup_account.py` | Operasi domain CLI yang dipakai panel |
| `scripts/bot_settings.py` | Registry/form schema, default, validasi, konfigurasi atomik |
| `botapp/service.py` | Use case panel, validasi input, adapter CLI tanpa shell |
| `botapp/jobs.py`, `worker.py` | Registri job, heartbeat, subprocess, stop kooperatif |
| `botapp/server.py` | Transport HTTP loopback, token sesi, file streaming, scheduler |
| `web/` | Navigasi, formulir, konfirmasi tindakan dan rendering response |

Tidak ada pemilihan video atau logika publish di UI. Worker menjalankan CLI
orchestrator yang sama dengan Terminal. Worker tidak membuka Chrome. Setelah jam mulai dan konten siap, orchestrator
mengambil slot global melalui scripts/browser_lifecycle.py, membuka profil Chrome
melalui launcher bila perlu, dan menutupnya setelah tiap video terverifikasi.
Slot memakai flock macOS/Linux; metadata port mempertahankan okupansi browser
yang tertahan setelah error atau worker mati. Jeda antarvideo tidak memegang slot. Login TikTok tetap
manual. Worker memakai subprocess sendiri sehingga UI tidak membeku saat bot
berjalan. Jangan menjalankan akun yang sama lewat Terminal/systemd/launchd dan
panel secara bersamaan.

## Data dan kompatibilitas

- DB bot `db/tiktok_bot.db` tetap menjadi sumber akun, konsep, produk, video dan
  riwayat. Panel tidak memigrasikan database lama secara otomatis.
- `db/control_panel.db` terpisah, hanya menyimpan job panel. Satu job aktif per
  akun, satu occurrence per jadwal, dengan constraint SQLite.
- `config/control-settings.json` berisi versi, konfigurasi global, override akun,
  dan jadwal. Penulisan memakai file sementara + fsync + replace.
- `.env` tetap untuk rahasia. Form kosong mempertahankan key; penyimpanan atomik
  hanya mengubah key yang diisi pengguna dan mempertahankan variabel lain.
- `scripts/project_paths.py` menyediakan root data. Default checkout ini;
  `TIKTOK_BOT_HOME` bisa diarahkan ke folder fixture. Code/schema tetap berasal
  dari checkout, data tidak boleh bocor ke proyek asli selama tes.

Orchestrator membaca konfigurasi pada awal proses. Perubahan UI berlaku pada
proses berikutnya, tidak mengubah parameter proses yang sudah berjalan. Model
caption global mengikuti .env GEMINI_MODEL jika belum diatur lewat panel;
konfigurasi model di panel mengambil prioritas sesudah disimpan.

## Jadwal lokal

Scheduler panel hanya berjalan selama server aktif, setiap lima detik. Waktu
mengikuti jam lokal Mac; hari menggunakan 0=Senin sampai 6=Minggu. Satu occurrence
menggunakan account + tanggal/jam. Jadwal terlewat tidak dibackfill. Menutup tab
browser tidak menghentikan server; menutup server menghentikan scheduler, tetapi
worker yang sudah dimulai tetap berjalan dan dapat dipantau ketika panel dibuka
kembali. Stop proses dari UI untuk menghentikan worker dengan sengaja.

Job/claim tidak di-reset otomatis saat aplikasi restart. Klaim yang hasilnya belum
pasti harus diperiksa lewat Antrean dan TikTok. Jika supervisor dipaksa mati,
status job dapat tertahan; jangan membersihkan DB kontrol/claim secara otomatis
untuk memaksa run baru. Periksa proses dan hasil TikTok melalui Codex terlebih
baru lakukan rekonsiliasi. Integrasi launchd/systemd lama tetap tersedia, tetapi
jangan mengaktifkan kedua scheduler untuk akun yang sama.

## Menambah fitur

1. Tentukan operasi domain dan kontrak hasilnya; perluas fungsi/CLI yang sudah ada.
2. Tambahkan field konfigurasi di registry, atau route use case di service.
3. Tambahkan elemen UI/form/tabel dan validasi yang membaca schema bila relevan.
4. Perbarui API version handshake jika kontrak tidak kompatibel.
5. Uji fixture data: kondisi berhasil, gagal, sumber terkunci, dan kompatibilitas
   alur yang sudah ada. Uji service melalui CLI dan transport HTTP bila berubah.
6. Perbarui README/runbook dan lakukan QA browser nyata saat tersedia.

Konfigurasi tambahan dengan tipe yang sudah didukung cukup menambah definisi
registry dan pemakaian di engine; form field otomatis mengikuti schema. Fitur
baru atau tipe field baru tetap membutuhkan penyesuaian UI dan tes bersama-sama.

## Pengujian

`venv/bin/python -m unittest discover -s tests -v`

Tes antrean menggunakan filesystem/SQLite nyata dan simulator CDP. Tes panel
memakai CLI nyata dengan root data sementara, request HTTP tanpa membuka port,
uji worker nyata dengan antrean kosong, serta DOM stub JavaScriptCore di macOS.
DOM stub menguji rendering/escaping/dialog; ini bukan screenshot atau QA visual.
Tes tidak memanggil provider AI, membuka Chrome, atau upload ke TikTok.

Server hanya bind `127.0.0.1`, memvalidasi Host, Origin dan token sesi untuk POST,
tidak mengaktifkan CORS dan tidak menampilkan secret. Token tidak menggantikan
login untuk hosting publik. Jika nanti ingin remote, rancang autentikasi dan
otorisasi sebagai fitur terpisah; jangan mengubah bind menjadi 0.0.0.0 saja.

## Sumber pribadi datar

`scripts/account_sources.py` menjadi implementasi tunggal validasi ID-produk.txt,
sinkronisasi produk dan rotasi persisten. `content_queue.py` tetap memiliki klaim
atomik. Pasangan di `content_assignments` dipertahankan pada kegagalan/retry;
source_folder menunjuk folder akun sehingga video dipilih lintas ID produk.
API v1 ditambah field akun source_path/product_ids/source_error dan route
sources/update tanpa mengubah kontrak akun konsep lama. Panel akun pribadi
mengelola daftar ID; edit produk lama ditolak karena file daftar sumber kebenaran.
Migrasi eksplisit memakai backup SQLite, pemeriksaan konflik, dan rollback folder.

## Musik Favorit

`scripts/music_favorites.py` memiliki selector editor hasil mapping browser serta
rotasi/pasangan musik persisten. Data disimpan di DB baru `db/music_rotation.db`,
tanpa mengubah schema/riwayat antrean lama. Orchestrator memanggilnya setelah
link produk terpasang dan sebelum begin_submission. Sesudah Save editor, timeline
diperiksa lewat membuka ulang Sounds dan keluar dengan Cancel tanpa edit; lalu
caption/link produk diperiksa ulang sebelum alur submit lama dilanjutkan.
Pengaturan boolean `music_enabled` berasal dari registry; default nonaktif.
Simulator menguji scroll/rotasi/retry/error; mapping browser nyata hanya membuktikan
satu favorit, penambahan audio, Save editor dan pembukaan ulang. Tes sampai
submit TikTok dengan musik belum dilakukan oleh suite lokal.
