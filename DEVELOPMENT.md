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
Slot memakai flock macOS/Linux dan menghitung hanya proses bot aktif. Metadata
port/PID bersifat diagnostik; Chrome manual, browser untuk pemeriksaan, dan
catatan worker mati tidak mengisi kuota. Lock OS terlepas saat proses mati;
keluar normal/error juga mengosongkan metadata sebelum menutup descriptor.
File lock tidak dihapus agar identitas inode tetap sama. Admission lock
menserialisasi alokasi dan menghitung lock aktif pada seluruh indeks, termasuk
indeks di atas batas baru setelah batas diturunkan. Pembatas tidak menghubungi
CDP atau menutup Chrome pengguna. Jeda antarvideo tidak memegang slot. Login TikTok tetap
manual. Worker memakai subprocess sendiri sehingga UI tidak membeku saat bot
berjalan. Jangan menjalankan akun yang sama lewat Terminal/systemd/launchd dan
panel secara bersamaan.

## Data dan kompatibilitas

- DB bot `db/tiktok_bot.db` tetap menjadi sumber akun, konsep, produk, video dan
  riwayat. Panel tidak memigrasikan database lama secara otomatis.
- `db/control_panel.db` terpisah, hanya menyimpan job panel. Satu job aktif per
  akun, satu occurrence per jadwal, dengan constraint SQLite.
  Tabel tambahan `run_test_options` menyimpan pilihan E2E per run secara atomik
  bersama job; worker meneruskannya ke `--test-options`. Validasi bersama ada di
  `scripts/diagnostic_options.py`. Job `draft_test` lama tetap didukung.
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

Tes Schedule memakai slot `batch_id=0` di metadata Schedule, tanpa membuka atau
menyelesaikan batch automasi. `begin_submission(test_scheduled=True)` mengikat
slot itu ke log dan tanggal kuota tes dalam transaksi yang sama. Slot tes tetap
ikut mencegah benturan waktu tayang. Waktu yang terlalu dekat ditolak, bukan digeser.

## Jadwal lokal

Scheduler panel hanya berjalan selama server aktif, setiap lima detik. Waktu
mengikuti jam lokal Mac; hari menggunakan 0=Senin sampai 6=Minggu. Satu occurrence
menggunakan account + tanggal/jam. Jadwal terlewat tidak dibackfill. Menutup tab
browser tidak menghentikan server; menutup server menghentikan scheduler, tetapi
worker yang sudah dimulai tetap berjalan dan dapat dipantau ketika panel dibuka
kembali. Stop proses dari UI untuk menghentikan worker dengan sengaja.

Proses hidup menggunakan `scripts/wake_recovery.py`: heartbeat satu detik
mendeteksi suspend lebih dari 15 detik; jendela/jeda memakai deadline jam
dinding. Setelah bangun bot menunggu koneksi TCP TikTok, lalu lanjut. Perintah
CDP memeriksa generasi wake sebelum mengirim dan setelah menerima. Persiapan
yang terputus sebelum begin_submission boleh diulang dengan claim/caption yang
sama setelah Chrome akun ditutup dan dibuka ulang, di dalam slot browser yang
sama. Sesudah begin_submission, kegagalan tetap uncertain dan tidak diulang.
Ini berlaku untuk CLI dan worker panel; tidak mengubah API atau mengaktifkan
jadwal. Log proses dan keterangan Jadwal menjelaskan perilaku sleep.

Job/claim tidak di-reset otomatis saat aplikasi restart. Klaim yang hasilnya belum
pasti harus diperiksa lewat Antrean dan TikTok. Jika supervisor dipaksa mati,
status job dapat tertahan; jangan membersihkan DB kontrol/claim secara otomatis
untuk memaksa run baru. Periksa proses dan hasil TikTok melalui Codex terlebih
baru lakukan rekonsiliasi. Integrasi launchd/systemd lama tetap tersedia, tetapi
jangan mengaktifkan kedua scheduler untuk akun yang sama.

Kuota harian dimiliki `content_queue.daily_post_count`: status success/draft/
submitting/uncertain dihitung per akun berdasarkan tanggal lokal. Orchestrator
membatasi daftar kerja ke sisa kuota, termasuk konsep lama dan retest, serta
memeriksa lagi setelah menunggu. `begin_submission(daily_limit=...)` memeriksa
kuota dalam transaksi BEGIN IMMEDIATE sebelum membuat catatan submit. CLI
`--limit` dibatasi oleh konfigurasi akun; restart tidak memberi kuota baru.

## Menambah fitur

### Mode output Schedule

`scripts/schedule_store.py` memiliki batch, kisi slot dan snapshot submit di DB
terpisah `db/schedule_posts.db`. Database ini di-ATTACH ke koneksi bot sebelum
transaksi; `begin_submission` menulis log utama dan snapshot tanggal kuota/slot
dalam transaksi yang sama. Tidak ada ALTER pada database bot produksi.
`daily_post_count` memakai tanggal kuota snapshot untuk Schedule, tanggal lokal
log untuk hasil lain; status scheduled/submitting/uncertain ikut kuota.
Snapshot per log tidak ditimpa ketika operator menyatakan tidak ada jadwal dan
video dicoba ulang. Backup konsisten harus mencakup kedua DB setelah worker berhenti.

`schedule_ui.py` mengatur radio, kalender dan jam melalui kontrol yang dipetakan,
memeriksa tanggal/jam/zona waktu sebelum submit, dan menghasilkan pemeriksaan
baris Posts. `daily_orchestrator.py` tetap memiliki upload, submit dan klaim;
dialog copyright ditangani untuk semua mode. Schedule memverifikasi ulang
tanggal/jam/zona waktu dan batas minimum sebelum konfirmasi satu kali. Mode
manual membatalkan dialog, menunggu copyright, lalu Save draft satu kali tanpa
membuat log submit baru. Publish langsung memakai konfirmasi yang sudah ada. Schedule baru dianggap berhasil
ketika baris dengan caption, penanda jadwal (teks atau ikon Alarm dengan GMT+7),
dan tanggal/jam cocok. UI hasil yang
belum dipetakan atau tidak cocok menahan klaim, tidak mengulang submit.

Batch mengambil satu browser slot sepanjang loop, melewati jeda antarvideo,
dan menutup Chrome hanya setelah selesai normal. Mode lain mempertahankan
perilaku menutup Chrome tiap video. Jadwal batch yang melewati tengah malam
tetap memakai quota_day batch, termasuk sesudah restart batch belum selesai.
Batch yang belum selesai mempertahankan konfigurasi semula. Batch selesai yang
dimulai ulang membaca konfigurasi baru, dengan slot terpakai tetap dipertahankan. Tes draft override tetap mode manual.

API routes tetap `/api/v1`, tetapi handshake `API_VERSION=2` membedakan enum
mode/status baru dari frontend lama. Field tambahan konfigurasi berasal dari
registry yang sama. Form akun menampilkan tiga radio mode dan hanya menampilkan
jeda tayang ketika Schedule dipilih; riwayat memperlihatkan scheduled_at/quota_day.

Tes `test_schedule_posts.py` memakai data sementara tanpa jaringan, meliputi
kuota bersama, rollback sebelum submit, restart, slot lewat, lintas tengah malam,
batch 12 video dengan satu browser, dan redirect yang bukan bukti berhasil.
Kontrol tanggal/jam sudah diuji lewat CDP browser nyata tanpa submit, dengan nilai
awal dipulihkan. Submit Schedule nyata tetap memerlukan pengujian operator.

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

## Kunci profil internal Chrome

Field `chrome_profile_directory` berasal dari registry settings, default `Default`,
dengan override per akun (`Profile N`). Resolver `chrome_profile_path.py` membaca
setting yang sama untuk launcher manual/panel/CLI; profil nondefault yang hilang
ditolak, tidak dibuat otomatis. Orchestrator membuat tab `chrome://version` dahulu,
memverifikasi path lengkap (root akun + direktori internal), baru menavigasi tab
itu ke TikTok. Ini juga memeriksa browser yang CDP-nya sudah hidup; flag launcher
saja tidak cukup saat admin membuka beberapa profil. Path tidak sesuai menghentikan
persiapan dan mengikuti pelepasan klaim sebelum submit. Tidak ada migrasi atau
pemindahan cookie/profil. Penguncian profil bukan verifikasi ulang identitas login
TikTok setiap kali posting; operator harus memverifikasi identitas sebelum memilih.

Chrome yang sudah terhubung tetap dipanggil melalui launcher untuk pilihan profil
nondefault sebelum pemeriksaan tab. Profil yang hilang ditolak sebelum tab dibuat;
jika tab baru masuk ke profil lain, hanya tab diagnostik milik percobaan itu yang
ditutup, tanpa menutup tab kerja admin. Kegagalan ini tidak menjalankan upload.


## Audit ketahanan 2026-11-12

`browser_process.py` merekam proses browser baru melalui SystemInfo.getProcessInfo
dan token ps (waktu mulai + executable), lalu memverifikasi keluarnya proses setelah
Browser.close. Fallback SIGTERM memeriksa ulang token PID tepat sebelum signal;
PID yang berubah/unknown dan endpoint instance yang berbeda tidak ditutup.
Metadata hanya hidup selama worker pemiliknya; browser manual tidak diadopsi.
Tes Chrome kosong dengan profil sementara membuktikan proses aplikasi keluar.

Lihat AUDIT-2026-11-12.md untuk temuan dan bukti uji. prepare_browser kini
mengembalikan bool kepemilikan: True hanya ketika proses ini membuka browser
baru; Chrome yang sudah ada dianggap manual. Orchestrator mempertahankan flag
selama batch dan hanya menutup Chrome miliknya. Gagal sebelum submit membersihkan
Chrome milik bot; hasil uncertain dan Chrome manual dipertahankan.

send_cdp memeriksa error protokol dan memiliki deadline 30 detik, termasuk jika
event terus masuk. Input.enable dihapus karena tidak ada dalam CDP. Semua
penutupan tab punya timeout dan post_one_video menutup socket dalam finally.
Main mengembalikan hasil task, lalu entrypoint menerjemahkannya ke exit code.
Supervisor meneruskan PID kepada anak; heartbeat meminta SIGINT bila parent hilang.
State klaim tetap menentukan apakah aman melepas video atau harus menahannya.

Mutasi konfigurasi service memakai configuration_lock berbasis flock sebelum
read-modify-write agar panel dan CLI tidak saling menimpa. Simpan pengaturan
dilakukan sebelum perubahan mode; kegagalan CLI mengembalikan konfigurasi lama.
Ini kompensasi kegagalan sinkron, bukan transaksi crash-atomic lintas JSON/SQLite.
Recovery setelah putus daya tetap memerlukan pemeriksaan operator.
