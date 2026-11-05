# Panduan perubahan proyek

Baca README.md dan CLAUDE.md sebelum mengubah alur bot. Panel lokal
ada di `botapp/` dan `web/`; detail batas modul ada di DEVELOPMENT.md.

- Aturan produk, konsep, klaim, posting, dan arsip hanya punya satu implementasi
  domain di scripts/. Panel memanggil CLI/fungsi yang sama; jangan membuat versi
  aturan terpisah di JavaScript atau query yang melewati klaim antrean.
- Setiap penambahan/perubahan fitur harus ditinjau untuk CLI, API panel, UI,
  validasi, dokumentasi, dan tes. Jika ada perubahan mayor, selesaikan perubahan
  UI dalam pekerjaan yang sama; jangan meninggalkan tombol/form yang tidak cocok.
- Konfigurasi umum didefinisikan di scripts/bot_settings.py (registry field,
  default, tipe, batas, bantuan). UI membaca schema dari backend. Jangan
  menduplikasi default atau batas di banyak file.
- API panel berversi `/api/v1`; perubahan kontrak yang tidak kompatibel harus
  memperbarui frontend dan version handshake sekaligus.
- Jangan overwrite .env, profil Chrome, video, atau data lama. Migrasi yang
  menyentuh data harus punya backup, pengecekan konflik, dan uji rollback.
  Panel tidak melakukan migrasi DB bot yang sudah ada secara otomatis.
- Jadwal/mulai proses harus merupakan tindakan pengguna. Saat pengembangan,
  jangan mengaktifkan jadwal atau menjalankan publish/upload sungguhan sebagai
  tes otomatis. Gunakan fixture DB/filesystem, simulator CDP, dan worker tanpa
  konten. Nyatakan terpisah verifikasi lokal dan E2E TikTok nyata.
- Rahasia API hanya di .env; response panel hanya status terisi/kosong. Jangan
  memasukkan key ke argv, log, HTML, atau konfigurasi JSON.
- Pertahankan macOS Intel dan Linux. Gunakan subprocess argv, bukan shell string.
  `TIKTOK_BOT_HOME` mengisolasi data saat pengujian dan subprocess panel.

Cek perubahan dengan `venv/bin/python -m unittest discover -s tests -v` dan
`git diff --check`. QA visual browser perlu dilakukan ketika lingkungan mendukung;
jangan menyebut DOM stub/simulator sebagai verifikasi browser nyata.
