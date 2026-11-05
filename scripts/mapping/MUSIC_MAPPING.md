# Mapping musik TikTok Studio — 6 November 2026

Mapping browser nyata pada akun `infosehat.ofc`, CDP 9221. Ini catatan
eksplorasi yang mendasari `scripts/music_favorites.py` dan pengaturan akun
`music_enabled`. Default fitur nonaktif. Integrasi submit diuji melalui simulator,
bukan submit TikTok otomatis pada saat mapping.

## Perilaku yang diminta

- Musik aktif/nonaktif di Pengaturan masing-masing akun.
- Sumber musik: Favorites yang diisi operator pada akun TikTok tersebut.
- Pemilihan berurutan, lalu mengulang; posisi perlu bertahan antarproses.
- Volume mengikuti bawaan TikTok, tanpa mengubah suara asli/volume/fade.
- Pemilihan dan penerapan musik terjadi sebelum submit publish/draft.
- Implementasi domain tetap di scripts/, UI membaca schema bot_settings.py.

## Hasil pemetaan nyata

1. Pada halaman upload yang sudah memiliki video, tombol `Sounds` membuka editor.
   Pilih berdasarkan teks persis dan hanya klik jika target tunggal. Jangan
   mengandalkan koordinat layar tetap atau tombol musik di navigasi Studio.
2. Editor memiliki `.clip-forge-editor-container` dan panel
   `.MusicPanelContainer__root` dengan input `Search sounds`.
3. Tombol `Favorites` berada di panel musik. Panel tab aktif ditandai
   `[role="tabpanel"][data-state="active"]`.
4. Baris musik: `.MusicPanelMusicItem__wrap`.
   Judul lengkap ada di `.MusicPanelMusicItem__infoBasicTitle`; deskripsi
   durasi/artis ada di `.MusicPanelMusicItem__infoBasicDesc`.
   Jangan memakai teks judul yang terpotong secara visual sebagai identitas.
5. Tambah lagu melalui `svg[data-icon="plus-bold"]` di baris yang dipilih.
   Tombol ikon ini bukan elemen `<button>`, sehingga pencarian button saja
   tidak menemukannya. Target harus dibatasi pada baris di Favorites aktif.
6. Sesudah ditambah, lagu terverifikasi di timeline melalui
   `.AudioClip__title` di dalam `.BaseClip__root`.
   `data-mov-id` adalah identitas klip editor, bukan bukti ID katalog musik yang
   stabil untuk rotasi antarvideo.
7. Pengaturan audio muncul sesudah lagu ditambah. Volume bawaan yang diamati
   bernilai `0 dB`, fade-in/fade-out `0`. Tidak ada nilai yang diubah.
8. Tombol `Save` dan `Cancel` editor berada di
   `.clip-forge-editor-header-right` di dalam `.TopBar__root`.
   `Save` editor berbeda dari tombol upload `Save draft` dengan
   `data-e2e="save_draft_button"`, dan `Post` dengan
   `data-e2e="post_video_button"`.

## Batas verifikasi dan posisi terakhir

- Draft lama yang sudah terbuka dipakai untuk inspeksi; tidak ada upload baru.
- Favorites saat inspeksi berisi satu audio berdurasi 18 detik; video berdurasi
  sekitar 29 detik. Preview memperlihatkan klip musik lebih pendek dari video.
- Sesuai instruksi operator, Save editor sudah diklik satu kali. Editor tertutup
  dan halaman kembali ke upload; preview sempat menampilkan Loading.
- Verifikasi dilakukan dengan membuka Sounds lagi: satu `.AudioClip__title`
  masih berisi judul audio favorit yang dipilih. Ini memverifikasi perubahan
  musik bertahan setelah Save editor tanpa mengandalkan label "Original sound"
  pada mockup preview upload, yang masih menampilkan nama akun.
- Editor verifikasi kemudian ditutup melalui Cancel tanpa perubahan tambahan.
  Posisi terakhir adalah halaman upload dengan musik yang sudah diterapkan.
- Save draft dan Post **belum diklik**. Tidak ada submit baru ke draft/publik
  yang dilakukan dalam mapping ini.
- Perlu menguji daftar beberapa favorit, pemuatan/scroll daftar, kondisi kosong,
  lagu tidak tersedia, retry tanpa duplikasi musik, dan rotasi persisten.
- Jangan menghapus atau mengganti draft/video/riwayat lama untuk tes. Uji bot
  memakai fixture/simulator; submit TikTok nyata tetap tindakan operator.
