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

Pada Mac ini, panel sudah diatur otomatis lewat LaunchAgent
`~/Library/LaunchAgents/local.ruangkonten.panel.plist`. Setelah restart dan login,
cukup buka **http://127.0.0.1:8765**, tanpa Terminal. launchd menyalakan kembali
panel jika prosesnya berhenti. Bot tetap dijalankan dari kartu akun atau jadwal
panel yang sudah diaktifkan. Jangan jalankan server panel manual kedua pada port
yang sama. Log layanan: `logs/panel-service.log`.

Untuk menyiapkan ulang file layanan setelah pindah lokasi proyek, gunakan
`venv/bin/python scripts/prepare_panel_launchd.py`; perintah ini hanya menghasilkan
plist di `config/launchd/`, tidak memasang atau mengaktifkannya. LaunchAgent panel
terpisah dari layanan automasi akun `local.tiktokbot.*`.

Urutan menu: **Ringkasan → Akun TikTok → Riwayat & proses → Konten & jadwal**,
diikuti Diagnostik dan Pengaturan. Ringkasan menampilkan total publish, Schedule,
dan draft sepanjang riwayat bot; kartu akun menampilkan hitungan masing-masing.
Publish & Schedule tidak mencakup draft, kegagalan, atau hasil belum pasti.
Schedule berarti berhasil dijadwalkan, belum diverifikasi sudah tayang. **Konten & jadwal** berisi tiga tab:
**Produk & video** untuk stok, **Antrean video** untuk status dan hasil tertahan,
serta **Jadwal mulai bot** untuk waktu mulai automasi. Jam tayang Schedule post
tetap di pengaturan akun. Jarak minimum dari mulai posting adalah 35 menit,
dibulatkan ke atas per 5 menit: mulai 09:03 → paling awal 09:40 WIB. Tautan halaman lama tetap dapat dibuka.

Panel menyediakan akun/profil Chrome, konsep/anggota, produk/ID TikTok Shop,
unggah MP4/MOV ke folder lokal, scan antrean, tes E2E, mulai/hentikan proses,
log, riwayat, rekonsiliasi video tertahan, jadwal, konfigurasi orchestrator per
akun, model caption dan API key tersamarkan. Upload di halaman Produk
hanya menyalin ke disk, bukan ke TikTok. Tombol Aktifkan Automasi pada akun mode Publish langsung melakukan posting
nyata setelah konfirmasi UI. Tes draft tetap upload sungguhan tetapi mode utama
akun tidak berubah. Login TikTok tetap dilakukan manual di Chrome khusus akun.

Batas harian dihitung per akun menurut tanggal lokal Mac, meliputi publish,
draft, Schedule, serta submit yang hasilnya belum pasti. Menghentikan/memulai ulang proses
atau sleep tidak mereset hitungan. Contoh: batas 5 dan sudah berhasil 2, maka
sisa maksimal 3. Berlaku untuk folder pribadi, konsep lama, dan tes draft/ulang.
Opsi CLI `--limit` hanya dapat memperkecil batas dari pengaturan akun.

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
(1 jam); default jeda acak 60–90 menit. Setiap klik browser diberi jeda sesuai **Jeda langkah browser**, default 1 detik,
selain penantian kesiapan kontrol/animasi. Jeda ini untuk kestabilan interaksi,
bukan jaminan menghindari deteksi automasi. Pengaturan khusus akun berlaku pada proses
berikutnya tanpa menjalankan posting atau mengaktifkan jadwal. Mode **Simpan
draft** / **Publish langsung** / **Schedule post** dipilih dengan tiga pilihan berdampingan di dialog
yang sama, tanpa checkbox konfirmasi saat menyimpan mode. Kolom **Nama link
produk** mengatur teks yang tampil pada link produk video untuk akun itu (maksimal
30 karakter). Default tetap `Promo Tiktok Disini`. Teks berlaku untuk seluruh ID
produk akun, untuk draft/publish/tes draft, mulai proses berikutnya.

**Musik dari Favorit** di pengaturan akun memakai pilihan Aktif/Nonaktif, default
Nonaktif. Saat aktif, bot membuka Sounds → Favorites, memilih satu lagu berurutan
lalu mengulang, memakai volume/fade bawaan TikTok, menyimpan editor, dan memeriksa
ulang timeline. Setelah itu bot melanjutkan Post atau Save draft sesuai mode akun.
Penanganan tour berlaku sepanjang editor musik: sebelum membaca kontrol,
memindai Favorit, menambah lagu, dan memverifikasi Save, bot menutup tour yang
dikenali melalui tombolnya (misalnya Got it), tanpa klik area luar, lalu membaca
ulang posisi kontrol. Tour yang tidak bisa ditutup tetap
menghentikan proses sebelum submit, tanpa menambahkan lagu berulang.
Caption dan link produk diperiksa kembali sebelum submit. Isi Favorites lewat
TikTok pada masing-masing akun terlebih dahulu. Favorit kosong, lagu pasangan
hilang, atau editor gagal disimpan menghentikan proses sebelum submit.
Tombol editor yang terpotong tepi jendela diklik pada bagian yang masih terlihat.
Jika seluruh tombol Save berada di luar layar, perbesar jendela Chrome atau
perkecil zoom halaman dengan Cmd+- (macOS) / Ctrl+- (Linux), lalu mulai ulang
proses akun setelah proses sebelumnya selesai. Retry mempertahankan pasangan lagu.

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
Jadwal baru nonaktif sampai diaktifkan pengguna. Jadwal mulai yang terlewat saat
Mac sleep tidak dijalankan otomatis. **Proses yang sudah aktif lanjut otomatis
setelah Mac bangun**, selama proses/Terminal tidak ditutup atau Mac dimatikan.
Jeda dihitung sampai jam tujuan: bila jeda habis saat sleep, bot melanjutkan
setelah bangun dan koneksi internet tersedia; tidak mengejar beberapa posting
sekaligus. Jeda berikutnya dihitung dari posting yang baru selesai. Jika sleep
memutus persiapan upload, bot membuka ulang Chrome dan mencoba video yang sama
dengan klaim, caption, ID produk, dan pasangan musik tetap. Jika submit sudah
dimulai dan hasil belum terverifikasi, video ditahan untuk diperiksa di TikTok;
tidak diulang otomatis. Deteksi sleep memakai jeda heartbeat lebih dari 15 detik.
Perilaku ini berlaku pada proses yang dimulai setelah pembaruan kode ini.
Jadwal tidak boleh lebih awal dari jam mulai orchestrator akun. Setelah jam mulai tercapai dan video siap,
orchestrator membuka Chrome khusus jika CDP belum terhubung. Menutup tab browser tidak menghentikan server/worker;
menutup server menghentikan jadwal, tetapi job yang telah dimulai tetap berjalan.
Gunakan Hentikan di Riwayat & proses bila ingin menghentikan job secara sengaja.
Halaman ini memisahkan proses aktif dan proses sebelumnya. Hasil setiap video
ada pada tabel Riwayat video; buka Lihat catatan untuk membaca pesan error lengkap.
Jangan menjalankan scheduler panel dan launchd/systemd pada akun yang sama.

Perubahan konfigurasi berlaku pada proses berikutnya. UI dan CLI memakai schema
pengaturan yang sama (`scripts/bot_settings.py`). Untuk setiap video: tunggu jam
mulai/jeda tanpa Chrome → buka Chrome khusus akun → upload dan verifikasi hasil →
simpan status/arsip → tutup Chrome yang dibuka oleh proses bot itu. Chrome yang
sudah dibuka manual tetap dipertahankan. Video berikutnya membuka lagi
profil yang sama dengan sesi login tersimpan. Profil internal Chrome dikunci melalui
**Pengaturan akun → Pengaturan lanjutan → Profil internal Chrome** (`Default`
atau `Profile N`). Launcher panel dan CLI memakai pilihan yang sama. Sebelum
membuka halaman upload, bot memeriksa path profil tab baru melalui
`chrome://version`; profil yang berbeda menghentikan proses tanpa upload.
Jika admin berpindah profil saat Chrome masih terbuka, gunakan **Buka Chrome**
pada kartu akun untuk memilih kembali profil yang dikunci sebelum memulai ulang.
Jangan mengganti pilihan hanya berdasarkan nama tampilan Chrome; pastikan username
pada TikTok Studio dan path di `chrome://version` dalam profil yang sama.
Contoh CLI (pilih profil akun yang telah diverifikasi):

```bash
venv/bin/python scripts/control.py set --account akun6 --values '{"chrome_profile_directory":"Profile 19"}'
```

 Antrean kosong, batas harian habis,
atau caption gagal tidak membuka Chrome. Kegagalan sebelum submit menutup Chrome
milik proses bot setelah bukti diagnostik direkam. Hasil ambigu mempertahankan
browser untuk pemeriksaan. Chrome manual tidak ditutup. Slot dilepas saat proses
bot selesai atau berhenti, meskipun Chrome masih terbuka.
Preview tanpa submit juga tetap terbuka untuk ditinjau.

**Pengaturan → Resource Chrome** membatasi browser otomatis yang aktif bersamaan,
default 2 untuk seluruh akun (bisa diubah 1–10). Akun lain menunggu slot tanpa
membuka browser; jam aktual bisa mundur jika banyak akun siap bersamaan. Browser
yang dibuka manual tidak dihitung dan tidak ditutup oleh pembatas ini. Slot
dihitung dari proses bot yang memegang lock, bukan dari jendela atau koneksi
Chrome. Catatan slot lama dari proses selesai/crash tidak menghalangi akun lain.
Panel dan CLI memakai pembatas
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

## Schedule post di TikTok

Di **Akun TikTok → roda gigi → Aksi akhir**, pilih **Schedule post**, lalu isi
**Jam tayang pertama (WIB)** dan **Jeda tayang (menit)**. Jam dan jeda tayang harus
kelipatan 5 menit. Simpan pengaturan tidak menjalankan bot. Aktifkan Automasi
ketika siap; konfirmasinya mencakup penyimpanan video di server TikTok untuk
tayang otomatis. Dialog Allow milik TikTok ditangani hanya ketika mode Schedule
yang sudah diaktifkan memerlukan persetujuan itu.

**Mulai posting pukul** tetap menentukan kapan proses upload boleh mulai.
Halaman **Jadwal** mengatur kapan bot mulai bekerja; waktu tayang Schedule diatur
terpisah pada kartu akun. Bot menyelesaikan caption, link produk dan musik,
memilih tanggal/jam, lalu klik Schedule. Setelah hasil terverifikasi, bot langsung
mengunggah video berikutnya di Chrome yang sama, tanpa jeda posting 60–90 menit.
Chrome menempati satu slot resource sepanjang batch dan ditutup setelah batch
selesai. Error sebelum submit menutup Chrome yang dibuka bot; hasil ambigu
mempertahankannya untuk pemeriksaan. Chrome yang sudah dibuka manual tidak ditutup.

Contoh jam pertama 17:30 dengan jeda 30 menit menghasilkan slot 17:30, 18:00,
18:30, dan seterusnya. Bot melewati slot yang sudah lewat atau terlalu dekat,
tetap mengikuti kisi jam pertama + kelipatan jeda. Untuk kompatibilitas video
belanja, pemilihan slot menyisakan minimal 35 menit (30 menit + cadangan submit
5 menit), sehingga upload selesai 17:00 akan melewati 17:30 ke 18:00.
Referensi batas video belanja: [TikTok Shop](https://seller-us.tiktok.com/university/essay?knowledge_id=7651565985810231).
Kalender TikTok menjadi pemeriksaan akhir batas tanggal; tanggal nonaktif,
zona waktu selain GMT+7, atau kontrol yang berubah menghentikan proses sebelum
submit. Batas akun/region dapat berbeda; bot tidak melewati validasi TikTok.

Pemilih Schedule mendukung dua tampilan Studio: combobox dan kolom tanggal/jam
readonly. Pada tampilan lama bot memilih tanggal kalender serta jam/menit melalui
kontrolnya, menunggu animasi selesai, lalu memverifikasi ulang tanggal, jam, WIB,
dan tombol Schedule. Kontrol ambigu atau nilai yang tidak cocok membatalkan submit.

Batas harian dipakai bersama oleh publish, draft, Schedule, dan submit yang belum
pasti. Batas 12 dengan 2 publish dan 1 draft menyisakan 9 Schedule. Tanggal tayang
boleh melewati tengah malam; seluruh batch tetap memakai tanggal kuota saat
batch dimulai, termasuk jika upload-nya melewati tengah malam. Batch yang terputus
melanjutkan tanggal kuota dan urutan slot yang tersimpan; restart tidak mereset
kuota. Batch yang terputus mempertahankan pengaturan semula. Setelah batch selesai,
proses berikutnya membaca pengaturan baru tanpa mereset kuota atau slot terpakai.

Riwayat menampilkan **Terjadwal**, **Tayang WIB**, dan **Kuota**; Terjadwal berarti
TikTok menerima jadwal, bukan bukti video sudah tayang. Video baru diarsipkan
setelah terverifikasi. Redirect saja tidak cukup: bot mencocokkan caption,
penanda jadwal (teks atau ikon Alarm) dan tanggal/jam pada baris Posts. Jika belum dapat dicocokkan,
video ditahan sebagai **Perlu diperiksa** dan batch berhenti. Setelah menghentikan
proses dan memeriksa TikTok, gunakan Antrean → Periksa hasil → **Sudah terjadwal**
hanya jika tanggal/jam sesuai catatan. Tidak ada retry submit otomatis.

Metadata ada di `db/schedule_posts.db`; jangan dihapus untuk mengulang slot.
Backup/restore DB bot harus menyertakan DB ini, ketika semua proses berhenti.
Tidak ada migrasi otomatis pada schema DB bot lama. Helper finalisasi video lama
`submit_current_video.py` tidak mendukung Schedule dan menolak mode tersebut;
gunakan orchestrator/panel. Tes draft tetap memaksa draft walaupun mode akun Schedule.

Dialog **Continue to post?** ditangani pada ketiga mode. Publish langsung
melanjutkan konfirmasi satu kali. Schedule hanya melanjutkan jika pilihan
tanggal/jam dan WIB tetap cocok serta waktunya masih valid. Simpan draft
memilih Cancel, menunggu copyright selesai (maksimal 15 menit), lalu mencoba
Save draft satu kali; tidak menggantinya dengan publish. Dialog yang tidak
dikenali atau pemeriksaan gagal tetap menahan hasil untuk diperiksa.

Implementasi diuji dengan SQLite/filesystem sementara, simulator alur batch,
dan browser nyata untuk form panel serta kontrol tanggal/jam. Submit Schedule
sungguhan dan pemeriksaan video setelah tayang belum diuji otomatis.
Setelah pembaruan, restart server panel ketika aman dan muat ulang tab; handshake
frontend/backend sekarang bernilai 2, dengan endpoint tetap `/api/v1`.

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
proses hari itu berhenti. Tour onboarding yang dikenali dapat ditutup melalui
Got it/Skip/Done di dalam kontainer tour yang sesuai. Popup **New editing features
added** dan **Preview your video on your phone** juga dikenali melalui judul dan
deskripsinya, lalu ditutup melalui Got it. Jika label tombol berbeda, bot dapat
mengklik backdrop di luar tour setelah hit-test memastikan titik tersebut mengenai
overlay, bukan tombol/form halaman. Bot tidak menekan tombol
Next/OK/Close secara umum pada dialog lain. Popup tidak dikenal tetap menghentikan
proses; bukti tampilan tersimpan di `logs/diagnostics/` dan ringkasan penghalang
muncul di log untuk mapping berikutnya. Dialog Add link diverifikasi sebelum memilih produk;
klik Add/Next yang tidak membuka dialog dicoba ulang secara terbatas, tanpa submit. Akun lain boleh memakai video berikutnya dari konsep
tersebut; tidak ada jaminan pembagian jumlah video sama rata antar-akun.

Mode auto mengenali konfirmasi `Continue to post?` / `still checking your video`
dan klik Post now sekali, kemudian memverifikasi redirect. Dialog lain tidak
diklik otomatis. Script uji lama `full_test_post.py`/`test_save_draft.py` dan
launcher `boot_start_all.sh` dinonaktifkan untuk semua akun; gunakan panel atau
orchestrator. Jangan pakai script mapping untuk menjalankan antrean.

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

Di **Diagnostik**, pilih akun → **Pilih video untuk tes**, lalu pilih video,
aksi akhir **Simpan draft / Publish langsung / Schedule post**, dan musik dari
Favorit (**Ikuti akun / Aktif / Nonaktif**). Default tes adalah draft. Schedule
memerlukan tanggal/jam **WIB**, kelipatan 5 menit, minimal 35 menit dan maksimal
10 hari dari sekarang. Jam tes tetap: bila terlalu dekat saat upload selesai,
bot berhenti sebelum submit, tanpa menggeser jam otomatis.

Tes memproses satu salinan video melalui alur utama: caption, link produk,
musik jika aktif, copyright, submit, verifikasi, dan arsip. Kuota harian dan
penahanan hasil belum pasti tetap berlaku. Video asli, pengaturan akun, dan
batch automasi Schedule tidak diubah. Musik aktif memakai rotasi Favorit akun;
salinan tes mendapat pasangan lagu sendiri. Publish dan Schedule adalah tindakan
nyata; periksa pilihan lalu klik **Tes & publish** atau **Tes & jadwalkan**.
Slot Schedule tes dicatat bersama riwayat; tanggal kuotanya hari tes dimulai.

CLI yang sama mendukung pilihan sekali jalan, misalnya tes publish tanpa musik:

```bash
venv/bin/python -u scripts/daily_orchestrator.py akun1 --publish --retest-content 1 --test-options '{"mode":"auto","music":"off"}'
```

Untuk Schedule gunakan `mode: "schedule"` dan `scheduled_at` berupa ISO datetime
WIB, misalnya `2026-11-13T17:30:00+07:00`; `music` menerima `account`, `on`, `off`.
`--test-options` tidak dapat digabung dengan `--test-draft`.

Saat halaman upload TikTok lambat, bot menunggu input video hingga 20 detik
sebelum gagal. Penantian ini tidak me-reload halaman atau mengunggah file berulang.

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


## Ketahanan proses dan batas operasional

Chrome yang dibuka bot ditutup sebagai **Quit aplikasi**, bukan hanya menutup
tab/jendela. Bot merekam PID, waktu mulai, executable, dan identitas endpoint
browser, lalu memeriksa prosesnya benar-benar berhenti. Jika `Browser.close`
belum menuntaskan Quit, fallback SIGTERM hanya ditujukan ke PID milik bot yang
identitasnya masih sama; tidak memakai killall atau SIGKILL. Chrome yang sudah
dibuka manual tetap dipertahankan. Hasil uncertain tetap tersedia untuk diperiksa.
Jika identitas proses tidak dapat direkam, log menyatakan keterbatasan verifikasi
dan penutupan hanya dilakukan lewat CDP.

Mode Schedule memakai satu instance sepanjang batch lalu Quit setelah selesai.
Publish/draft melakukan Quit selama jeda panjang dan membuka profil tersimpan
pada video berikutnya. Startup memakai CPU/disk sesaat; profil/login tidak dihapus.
Ikon yang dipin atau ditampilkan sebagai aplikasi terbaru oleh macOS bisa tetap
terlihat setelah aplikasi Quit; pengaturan Dock pengguna tidak diubah bot.

Panel dan CLI memakai batas file yang sama: MP4/MOV tidak kosong, maksimal 2 GB,
tanpa symlink. Panel memeriksa ruang untuk file serta cadangan 256 MB sebelum
menyalin. Ini tidak memvalidasi codec atau menjamin file bebas kerusakan.

Error task menghasilkan exit code gagal dan status job Gagal, bukan Proses selesai.
Worker menunggu penghentian anak; bila supervisor hilang, heartbeat anak meminta
penghentian aman. Recovery sleep dibatasi dua kali sebelum submit, tanpa mengulang
submit yang hasilnya belum pasti. Perintah Chrome memiliki batas tunggu; WebSocket
ditutup juga pada interupsi. Gangguan satu tick tidak mematikan scheduler panel.

Schedule memerlukan rollback journal untuk kedua file database. Database produksi
saat audit memakai DELETE; bot tidak mengubah mode journal otomatis. Setelah
reboot/SIGKILL, status job atau klaim mungkin tetap perlu diperiksa melalui Antrean;
tidak ada reset otomatis yang dapat menyebabkan posting ganda.

Hasil audit dan keterbatasan verifikasi: [AUDIT-2026-11-12.md](AUDIT-2026-11-12.md).
