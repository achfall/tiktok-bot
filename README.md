# TikTok Bot

Bot video affiliate multi-akun menggunakan Chrome DevTools Protocol. Caption
memakai Gemini dengan DeepSeek opsional sebagai fallback. Mendukung macOS Intel
(tanpa Homebrew) dan Linux. README ini berisi panduan operator; arsitektur pengembangan ada di DEVELOPMENT.md.

## Panel web lokal

Jalankan dari Terminal (tidak memerlukan Node/Homebrew):

```bash
~/tiktok-bot/venv/bin/python ~/tiktok-bot/scripts/start_panel.py
```

Lalu buka **http://127.0.0.1:8765**. Alternatif: double-click `Buka Panel.command`
di folder proyek. Terminal server perlu tetap terbuka; Ctrl+C menutup panel.
Port bisa diganti dengan `--port 8766`. Aplikasi hanya menerima koneksi lokal.

Panel menyediakan akun/profil Chrome, konsep/anggota, produk/ID TikTok Shop,
unggah MP4/MOV ke folder lokal, scan antrean, tes E2E draft, mulai/hentikan proses,
log, riwayat, rekonsiliasi video tertahan, jadwal, konfigurasi orchestrator per
akun, model caption dan API key tersamarkan. Upload di halaman Produk
hanya menyalin ke disk, bukan ke TikTok. Tombol Aktifkan Automasi pada akun mode Publish langsung melakukan posting
nyata setelah konfirmasi UI. Tes draft tetap upload sungguhan tetapi mode utama
akun tidak berubah. Login TikTok tetap dilakukan manual di Chrome khusus akun.

Batas video harian dan jeda posting diatur lewat tombol **Pengaturan** pada
kartu masing-masing akun di halaman **Akun TikTok** (ikon roda gigi di pojok kartu).
Dialog mengelompokkan **Jadwal & batas** dan **Konten & musik**; jeda langkah
browser tersedia di **Pengaturan lanjutan**, bersama field **Nama produk** untuk
label kartu akun (misalnya `jingsu` atau `manjakani`). Label kartu ini opsional dan
terpisah dari **Nama link produk**; tidak mengubah teks link pada video, ID produk,
caption, atau folder konten. Tombol Simpan/Batal tetap terlihat
saat form digulir. Jam mulai ditulis dalam
format 24 jam `HH:MM`, misalnya `10:00`, `21:00`, atau `10:30`. Jeda minimum dan
maksimum ditampilkan berdampingan. Jeda paling singkat 60 menit
(1 jam); default jeda acak 60–90 menit. Pengaturan khusus akun berlaku pada proses
berikutnya tanpa menjalankan posting atau mengaktifkan jadwal. Mode **Simpan
draft** / **Publish langsung** dipilih dengan dua tombol berdampingan di dialog
yang sama, tanpa checkbox konfirmasi saat menyimpan mode. Kolom **Nama link
produk** mengatur teks yang tampil pada link produk video untuk akun itu (maksimal
30 karakter). Default tetap `Promo Tiktok Disini`. Teks berlaku untuk seluruh ID
produk akun, untuk draft/publish/tes draft, mulai proses berikutnya.

**Musik dari Favorit** di pengaturan akun memakai pilihan Aktif/Nonaktif, default
Nonaktif. Saat aktif, bot membuka Sounds → Favorites, memilih satu lagu berurutan
lalu mengulang, memakai volume/fade bawaan TikTok, menyimpan editor, dan memeriksa
ulang timeline. Setelah itu bot melanjutkan Post atau Save draft sesuai mode akun.
Caption dan link produk diperiksa kembali sebelum submit. Isi Favorites lewat
TikTok pada masing-masing akun terlebih dahulu. Favorit kosong, lagu pasangan
hilang, atau editor gagal disimpan menghentikan proses sebelum submit.

Rotasi disimpan per akun di `db/music_rotation.db`, terpisah dari DB antrean bot;
tidak ada migrasi DB bot otomatis. Pasangan video–lagu dan giliran disimpan sebelum
lagu ditambahkan: retry tetap memakai lagu sama tanpa memutar ulang cursor.
Perubahan daftar Favorites berlaku untuk video baru, dengan cursor modulo panjang
daftar saat ini. Jangan hapus metadata rotasi untuk mengulang video. Lagu pendek
mengikuti durasi bawaan editor dan tidak dipanjangkan/diulang otomatis sepanjang
video. Diagnostik memakai salinan video dengan pasangan musiknya sendiri. Menu Pengaturan
utama hanya menampilkan Resource Chrome, Caption AI, dan API key; default internal
akun baru tetap tersedia. Beberapa akun boleh memiliki jam, batas, dan jeda yang
sama. Jika proses akun sedang berjalan/menunggu, pengaturan kartu hanya dapat
dilihat dan belum bisa disimpan; tunggu selesai atau hentikan proses dahulu.
Proses yang sudah dimulai memakai konfigurasi yang dibaca pada awal proses.

Jadwal panel mengikuti jam lokal dan **hanya berjalan saat server panel aktif**.
Jadwal baru nonaktif sampai diaktifkan pengguna. Mac harus menyala/login/tidak
sleep; jadwal terlewat tidak dijalankan otomatis. Jadwal tidak boleh lebih awal
dari jam mulai orchestrator akun. Setelah jam mulai tercapai dan video siap,
orchestrator membuka Chrome khusus jika CDP belum terhubung. Menutup tab browser tidak menghentikan server/worker;
menutup server menghentikan jadwal, tetapi job yang telah dimulai tetap berjalan.
Gunakan Hentikan di Riwayat & proses bila ingin menghentikan job secara sengaja.
Jangan menjalankan scheduler panel dan launchd/systemd pada akun yang sama.

Perubahan konfigurasi berlaku pada proses berikutnya. UI dan CLI memakai schema
pengaturan yang sama (`scripts/bot_settings.py`). Untuk setiap video: tunggu jam
mulai/jeda tanpa Chrome → buka Chrome khusus akun → upload dan verifikasi hasil →
simpan status/arsip → tutup Chrome secara normal. Video berikutnya membuka lagi
profil yang sama dengan sesi login tersimpan. Antrean kosong, batas harian habis,
atau caption gagal tidak membuka Chrome. Hasil gagal/ambigu tetap membuka browser
untuk pemeriksaan; slot browser tersebut baru tersedia setelah Chrome ditutup.
Preview tanpa submit juga tetap terbuka untuk ditinjau.

**Pengaturan → Resource Chrome** membatasi browser otomatis yang aktif bersamaan,
default 2 untuk seluruh akun (bisa diubah 1–10). Akun lain menunggu slot tanpa
membuka browser; jam aktual bisa mundur jika banyak akun siap bersamaan. Browser
yang dibuka manual tidak dibatasi tombol ini. Panel dan CLI memakai pembatas
lintas proses yang sama di `db/browser-slots/`; tidak menghapus klaim/riwayat.
CLI konfigurasi tersedia:

```bash
venv/bin/python scripts/control.py settings
venv/bin/python scripts/control.py set --values '{"max_per_day": 5}'
```

Data job panel berada di DB terpisah dan konfigurasi di `config/control-settings.json`.
Database/riwayat bot, profil Chrome, dan .env lama dipertahankan. Tidak ada jadwal
atau posting yang otomatis diaktifkan hanya karena panel dibuka.
Panduan pengembangan fitur/UI ada di DEVELOPMENT.md dan AGENTS.md.

## Folder konten pribadi per akun (default baru)

```text
content/
  infosehat.ofc/             # akun lokal yang sudah ada
    ID-produk.txt
    video-001.mp4
    video-002.mov
    sudah-dipakai/
  jingsu.id/                 # contoh akun baru, dibuat saat didaftarkan
    ID-produk.txt
```

Tim menambahkan MP4/MOV langsung ke folder akun. `ID-produk.txt` berisi satu
ID TikTok Shop per baris, tanpa judul, URL atau ID duplikat. Contoh:

```text
1737713282535425495
1737713282535425496
```

Bot scan saat proses dimulai, memilih video berdasarkan ID antrean (file satu
batch scan diurutkan menurut nama), lalu memasangkan ID produk secara berurutan
dan berulang A → B → A. Posisi rotasi dan pasangan video–ID disimpan di SQLite
sebelum upload; restart atau retry tidak mengganti pasangan yang sudah dibuat.
Mengedit daftar berlaku untuk video yang belum dipasangkan. Posisi numerik rotasi
lanjut modulo panjang daftar baru; pasangan lama tetap memakai ID tersimpan,
termasuk jika ID tersebut dihapus dari daftar. Tidak ada mode acak saat ini.
Semua video harus cocok dengan seluruh ID produk di daftar. Caption folder akun
bersifat generik karena ID produk bukan nama produk.

ID kosong/tidak valid menghentikan scan/proses sebelum browser dibuka oleh worker.
Panel **Akun TikTok → ID produk** juga bisa mengedit daftar. Setelah daftar diisi,
scan mendaftarkan produk secara otomatis; jangan gunakan pengaturan produk lama
untuk akun pribadi. File ID tetap di sumber, tidak ikut diarsipkan.

Setelah publish **atau draft terverifikasi**, video berstatus `used` dan dipindah
ke `sudah-dipakai/`. Arsip lama dipertahankan, termasuk subfolder arsip historis.
Tidak perlu menghapus konten lama. DB dan riwayat harus tetap disimpan. Scan tidak
membaca subfolder. Video identik yang disalin ulang ke stok/nama baru belum dicek
berdasarkan hash dan bisa diposting lagi. Jangan mengganti file yang sedang antre.
Klaim hasil ambigu tetap ditahan `uncertain`, tidak dicoba ulang otomatis.

Batas harian akun pribadi adalah jumlah video total, meskipun ID produk berulang.
Jeda posting, mode publish/draft dan jadwal tetap mengikuti pengaturan akun.
Satu akun tidak boleh dijalankan oleh dua launcher bersamaan.

Tambah akun (tanpa mengaktifkan posting/jadwal):

```bash
venv/bin/python scripts/setup_account.py akun2 --name jingsu.id
```

Ini membuat `content/jingsu.id/ID-produk.txt` kosong dan profil Chrome terpisah.
Isi daftar sebelum menjalankan scan/bot. Panel tambah akun memakai model ini. Tombol **Buka folder** pada
kartu akun membuka sumber `content/<nama-akun>/` langsung di Finder (macOS) atau
file manager (Linux), termasuk akun baru yang belum punya ID produk. Tambahkan
MP4/MOV di sana; scan dilakukan saat proses berikutnya dimulai. Menu Konsep
konten tidak ditampilkan pada panel; operasi konsep lama tetap tersedia lewat CLI.

**Diagnostik → Tes draft** memindai video akun terpilih, lalu menawarkan video
untuk dibuat salinannya dan diuji upload sampai
Save draft tanpa mengubah mode akun. **Mulai proses** dalam mode Simpan draft
memakai antrean asli dan mengarsipkan video setelah draft terverifikasi.
Kartu akun menampilkan status automasi: berjalan, menunggu proses, menghentikan,
menunggu jadwal aktif, atau tidak aktif. Status ini mengikuti job panel dan
jadwal, terpisah dari koneksi/login Chrome.

Migrasi akun lama, saat semua worker akun berhenti:

```bash
venv/bin/python scripts/migrate_account_source.py akun1
```

CLI membuat backup SQLite sebelum perubahan, menolak tujuan yang sudah ada,
konsep dengan beberapa anggota, beberapa folder produk lama, serta klaim
tertahan. Riwayat/status/ID konten tetap utuh; folder dipindah dan path diperbarui
dalam transaksi. Error membalik pemindahan folder. Migrasi berulang tidak
mengubah sumber yang sudah dikonversi. Jangan rename profil Chrome untuk sekadar
mengganti contoh username. Akun lokal saat ini tetap `infosehat.ofc`.

## Kompatibilitas konsep lama

Akun yang belum dimigrasikan tetap memakai `content/<konsep>/<produk>/` dengan
antrean bersama. CLI `--concept` masih tersedia untuk instalasi lama. Petunjuk
konsep dan registrasi produk di bawah berlaku khusus model lama tersebut;
akun pribadi baru cukup mengisi `ID-produk.txt`.

## Setup dan migrasi

Clone ke `~/tiktok-bot`, buat venv, lalu pasang requirements:

```bash
python3 -m venv venv
venv/bin/python -m pip install --only-binary=:all: -r requirements.txt
```

Buat `.env` dari `.env.example` hanya jika belum ada. Isi minimal satu API key
caption secara lokal, jangan commit rahasia. Model dapat dipilih lewat
`GEMINI_MODEL`; default mengikuti rekomendasi API saat setup.

Untuk database baru, `scripts/setup_account.py` membuat schema terbaru. Untuk
instalasi lama, hentikan orchestrator dan jalankan migrasi aditif:

```bash
venv/bin/python scripts/manage_concepts.py migrate
venv/bin/python scripts/manage_concepts.py create video-jahit
venv/bin/python scripts/manage_concepts.py assign akun1 video-jahit --move-existing
```

CLI migrate/assign membuat backup SQLite dahulu. `--move-existing` memindahkan
folder produk akun pribadi ke konsep dan memperbarui path tanpa mengubah
riwayat/status video. Tujuan yang sudah ada tidak ditimpa atau digabung otomatis.
Jika akun sudah menjadi anggota konsep, perubahan grup hanya mengubah sumber
akunnya; stok/arsip konsep lama tetap di konsep lama. Jangan menjalankan bot
ketika mengubah anggota konsep atau migrasi folder.

Konfigurasi lokal saat ini: `akun1` / Chrome `infosehat.ofc` memakai `video-jahit`.
Profil Chrome tetap `chrome-profiles/infosehat.ofc`; konten setelah migrasi pribadi di
`content/infosehat.ofc`. Jadwal otomatis belum diaktifkan.

## Menambah konsep, akun dan produk

Contoh berikut untuk akun baru, bukan perintah yang sudah dijalankan:

```bash
venv/bin/python scripts/manage_concepts.py create konsep-B
venv/bin/python scripts/setup_account.py akun2 --name jingsu.id --concept konsep-B
```

Ini membuat profil `chrome-profiles/jingsu.id` dan memakai sumber `content/konsep-B`.
Tidak dibuat folder konten pribadi `content/akun2`/`content/jingsu.id`. Akun baru
bermode `manual_review` dan tidak membuka browser atau mengaktifkan jadwal.
Port CDP = 9220 + nomor akun. Untuk akun berikutnya ganti akun3, dst.

Buka Chrome dan login TikTok yang sesuai nama folder:

```bash
bash scripts/start-chrome-profile.sh jingsu.id 9222
```

Masukkan video ke `content/konsep-B/Jingsu/`, lalu daftarkan ID produk untuk akun:

```bash
venv/bin/python scripts/manage_products.py add akun2 "Jingsu" 1737713282535425495
venv/bin/python scripts/manage_products.py list akun2
venv/bin/python scripts/manage_concepts.py list
```

Daftarkan produk pada setiap anggota yang boleh mengambil video produk tersebut.
Nama folder harus persis sama; ID TikTok Shop boleh berbeda sesuai akun. Jangan
edit DB/CSV untuk menambah atau mengganti produk. Gunakan `set-id`, `disable`,
`enable` pada CLI yang sama. Scan manual opsional:

```bash
venv/bin/python scripts/db/scan_contents.py akun2
```

## Menjalankan dan memilih publish/draft

```bash
venv/bin/python scripts/setup_account.py akun1 --publish-mode auto
venv/bin/python scripts/setup_account.py akun1 --publish-mode manual_review
venv/bin/python -u scripts/daily_orchestrator.py akun1 --publish --limit 1
```

Pilih salah satu mode, jangan menjalankan kedua baris mode sekaligus. `auto`
menekan Post, `manual_review` menekan Save draft. Tanpa `--publish`, bot tetap
upload tetapi berhenti setelah link terpasang dan melepas klaim tanpa arsip.
Aturan normal: satu video per produk per akun per hari, maksimal 15 produk,
mulai pukul 10.00 waktu lokal, jeda acak 60–90 menit. Jika satu langkah gagal,
proses hari itu berhenti. Akun lain boleh memakai video berikutnya dari konsep
tersebut; tidak ada jaminan pembagian jumlah video sama rata antar-akun.

Mode auto mengenali konfirmasi `Continue to post?` / `still checking your video`
dan klik Post now sekali, kemudian memverifikasi redirect. Dialog lain tidak
diklik otomatis. Script uji lama `full_test_post.py`/`test_save_draft.py` ditolak
untuk instalasi konsep; jangan pakai script mapping untuk menjalankan antrean.

## Klaim tertahan dan pemulihan

Kegagalan sebelum submit melepaskan video ke antrean. Sebelum tombol Post/Save
draft diklik, state `submitting` disimpan. Jika hasilnya ambigu, video menjadi
`uncertain` dan tidak boleh diambil akun lain. Jika proses mati mendadak, klaim
juga tidak dilepas otomatis; operator harus memeriksa TikTok dan menghentikan
proses akun sebelum rekonsiliasi.

```bash
venv/bin/python scripts/manage_queue.py list
venv/bin/python scripts/resume_post_confirmation.py akun1
```

Resume hanya untuk satu dialog pemeriksaan yang cocok dengan klaim `uncertain`.
Jika hasil diperiksa manual, catat hasil yang benar dengan salah satu perintah:

```bash
venv/bin/python scripts/manage_queue.py resolve ID_KONTEN --result success --checked-tiktok
venv/bin/python scripts/manage_queue.py resolve ID_KONTEN --result draft --checked-tiktok
venv/bin/python scripts/manage_queue.py resolve ID_KONTEN --result not-posted --checked-tiktok
```

`not-posted` hanya setelah dipastikan tidak ada post/draft, lalu mengembalikan
video ke antrean. Jika operasi arsip gagal, status tetap used dan log menampilkan
peringatan; jangan upload ulang. Perbaiki penyebab lokal lalu jalankan
`venv/bin/python scripts/archive_used.py akun1`.

## Verifikasi dan tes E2E

```bash
venv/bin/python scripts/check_local_setup.py
venv/bin/python -m unittest discover -s tests -v
```

Tes lokal memakai SQLite/filesystem nyata dan simulator CDP/API; tidak upload.
Meliputi konkurensi dua konsep/10 akun, isolasi sumber, pemulihan, arsip,
rollback migrasi, serta alur orchestrator sampai draft/publish/Post now.

Untuk tes TikTok nyata memakai salinan video lama ID 1 dan berakhir draft:

```bash
venv/bin/python -u scripts/daily_orchestrator.py akun1 --publish --retest-content 1 --test-draft
```

Tes ini upload sungguhan dan membuat draft baru, tanpa mengubah mode DB akun.
Baris/arsip lama tetap utuh; salinan tes punya ID konten baru. Publish ulang ke
publik memerlukan mode `auto` dan `--allow-retest-publish` sebagai pengganti
`--test-draft`. Jangan mengulang tes jika submit belum terverifikasi.

## Penjadwalan dan Chrome

macOS: `venv/bin/python scripts/prepare_launchd.py` membuat plist inert di
`config/launchd/`, tanpa install/load. Jadwal 10.00 waktu lokal; RunAtLoad dan
KeepAlive false. Runner memerlukan guard `config/allow-posting-<akun>` yang tidak
dibuat otomatis. Aktivasi dilakukan terpisah setelah konfigurasi dan tes nyata
selesai. Mac perlu menyala, user login, internet tersedia dan tidak sleep selama
proses. Jadwal yang terlewat dapat berjalan setelah bangun; launchd tidak
merestart paksa proses panjang yang masih berjalan seperti timer systemd Linux.

Linux tetap memakai systemd user services/timers. Jangan gunakan launcher
`boot_start_all.sh` lama karena dapat memulai orchestrator duplikat.
Launcher Chrome membaca path DB, mendukung nama akun internal/nama folder profil,
macOS `/Applications/Google Chrome.app` dan Linux google-chrome/chromium. Lokasi
lain bisa diatur melalui `CHROME_BIN`. Shortcut editor mendukung macOS/Linux.
Jangan hapus/reset profil Chrome yang sudah login. Tutup browser sebelum rename.
Di macOS, tombol Buka Chrome dan launcher memakai LaunchServices (`open`)
untuk membuka profil dalam sesi desktop dengan akses Keychain. Jika muncul
pesan profil terenkripsi/Relaunch atau login hilang setelah ditutup, tutup Chrome
khusus akun tersebut lalu buka lagi lewat panel. Izinkan akses Chrome Safe Storage
di dialog macOS jika diminta; login ulang lalu uji tutup/buka sebelum menjalankan bot.
Jangan menjalankan akun TikTok yang sama pada dua device bersamaan.

Referensi: [Apple launchd](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html)
dan [Chrome remote debugging dengan profil khusus](https://developer.chrome.google.cn/blog/remote-debugging-port?hl=en).
