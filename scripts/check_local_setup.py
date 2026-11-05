#!/usr/bin/env python3
"""Pemeriksaan lokal read-only. Tidak membuka browser, memanggil AI, atau upload."""
import sqlite3
from pathlib import Path
from dotenv import dotenv_values

BASE = Path.home() / 'tiktok-bot'


def main():
    problems = []
    env = dotenv_values(BASE / '.env')
    if not any(env.get(key) for key in ('GEMINI_API_KEY', 'DEEPSEEK_API_KEY')):
        problems.append('Isi minimal satu API key caption di .env')
    db = BASE / 'db/tiktok_bot.db'
    if not db.is_file():
        problems.append('Database belum dibuat')
    else:
        with sqlite3.connect(db.as_uri() + '?mode=ro', uri=True) as conn:
            if conn.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                problems.append('Integritas database gagal')
            columns = {row[1] for row in conn.execute('PRAGMA table_info(accounts)')}
            if 'caption_style' not in columns:
                problems.append('Database tidak memiliki accounts.caption_style')
            accounts = conn.execute('SELECT a.slug, a.cdp_port, a.chrome_profile_path,g.name FROM accounts a LEFT JOIN content_groups g ON g.id=a.content_group_id WHERE a.active=1').fetchall()
            if not accounts:
                problems.append('Belum ada akun aktif')
            for slug, port, profile_path, concept in accounts:
                profile = Path(profile_path)
                print(f'Akun {slug}, konsep {concept or "pribadi"}: port {port}, profil {"ada; login belum diverifikasi" if profile.is_dir() else "belum ada"}')
                if not profile.is_dir():
                    problems.append(f'Profil Chrome {slug} belum disiapkan/login')
            for table, column in [('products', 'folder_path'), ('contents', 'file_path')]:
                # Arsip used boleh dihapus manual; hanya antrean unused wajib tersedia.
                query = f'SELECT {column} FROM {table}'
                if table == 'contents':
                    query += " WHERE status='unused'"
                paths = [row[0] for row in conn.execute(query)]
                missing = sum(not Path(path).exists() for path in paths)
                if missing:
                    problems.append(f'{table}: {missing}/{len(paths)} path tidak ditemukan; periksa sumber konsep dan lokasi file')
            held = conn.execute("SELECT COUNT(*) FROM contents WHERE status IN ('claimed','submitting','uncertain')").fetchone()[0]
            if held:
                problems.append(f'{held} video terkunci; periksa manage_queue.py list')
            videos = conn.execute("SELECT COUNT(*) FROM contents WHERE status='unused'").fetchone()[0]
            if not videos:
                problems.append('Antrean video unused kosong')
    for problem in problems:
        print(f'BELUM SIAP: {problem}')
    if not problems:
        print('Pemeriksaan file lokal lolos; login dan UI TikTok belum diverifikasi.')
    return bool(problems)


if __name__ == '__main__':
    raise SystemExit(main())
