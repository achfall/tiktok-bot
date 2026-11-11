#!/usr/bin/env python3
"""Tambah akun lokal baru tanpa browser, upload atau aktivasi penjadwal."""
import argparse
from pathlib import Path
import re
import sqlite3

from project_paths import PROJECT_ROOT
from account_sources import DDL

BASE = PROJECT_ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('account', help='akun1, akun2, dst.')
    parser.add_argument('--publish-mode', choices=['auto', 'manual_review', 'schedule'], help='Ubah aksi akhir, tanpa menjalankan bot')
    parser.add_argument('--name', help='Nama TikTok untuk folder Chrome dan video akun baru')
    parser.add_argument('--concept', help='Konsep konten untuk akun baru (buat konsep dahulu)')
    args = parser.parse_args()
    folder_name = args.name or args.account
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*', folder_name):
        parser.error('Nama folder tidak valid')
    match = re.fullmatch(r'akun([1-9][0-9]*)', args.account)
    if not match:
        parser.error('Gunakan akun1, akun2, dst.')
    port = 9220 + int(match[1])
    if port > 65535:
        parser.error('Nomor akun terlalu besar')
    db = BASE / 'db/tiktok_bot.db'
    if not db.exists():
        db.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db) as conn:
            conn.executescript((Path(__file__).resolve().parent / 'db/schema.sql').read_text())
    with sqlite3.connect(db.as_uri() + '?mode=rw', uri=True) as conn:
        group = None
        if args.concept:
            group = conn.execute('SELECT id,folder_path FROM content_groups WHERE name=?', (args.concept,)).fetchone()
            if not group:
                raise SystemExit('Konsep belum ada; gunakan manage_concepts.py create')
        existing = conn.execute('SELECT chrome_profile_path FROM accounts WHERE slug=?', (args.account,)).fetchone()
        if existing:
            if args.concept:
                raise SystemExit('Akun sudah ada; gunakan manage_concepts.py assign untuk mengubah konsep.')
            if args.name and Path(existing[0]).name != args.name:
                raise SystemExit('Akun sudah ada; rename folder perlu migrasi terpisah.')
            if args.publish_mode:
                conn.execute('UPDATE accounts SET publish_mode=? WHERE slug=?', (args.publish_mode, args.account))
                print(f'{args.account}: publish_mode={args.publish_mode}; bot tidak dijalankan.')
            else:
                print(f'{args.account} sudah ada; konfigurasi dipertahankan.')
            return
        columns = {row[1] for row in conn.execute('PRAGMA table_info(accounts)')}
        if 'caption_style' not in columns:
            raise SystemExit('Schema lama: caption_style belum ada; migrasikan schema dahulu.')
        if conn.execute('SELECT 1 FROM accounts WHERE cdp_port=?', (port,)).fetchone():
            raise SystemExit(f'Port {port} sudah dipakai akun lain')
        profile = BASE / 'chrome-profiles' / folder_name
        if conn.execute('SELECT 1 FROM accounts WHERE chrome_profile_path=?', (str(profile),)).fetchone() or profile.exists() or (not group and (BASE / 'content' / folder_name).exists()):
            raise SystemExit('Nama folder sudah digunakan; tidak ada data ditimpa.')
        conn.executescript(DDL)
        conn.execute('INSERT INTO accounts (slug, chrome_profile_path, cdp_port, publish_mode, caption_style, content_group_id) VALUES (?, ?, ?, ?, ?, ?)',
                     (args.account, str(profile), port, args.publish_mode or 'manual_review', 'generic', group[0] if group else None))
        if not group:
            conn.execute('INSERT INTO account_sources(account_id,folder_path) VALUES(?,?)',(conn.execute('SELECT id FROM accounts WHERE slug=?',(args.account,)).fetchone()[0],str(BASE/'content'/folder_name)))
    content_folder = Path(group[1]) if group else BASE / 'content' / folder_name
    for folder in (profile, content_folder, BASE / 'logs'):
        folder.mkdir(parents=True, exist_ok=True)
    if not group:
        with (content_folder/'ID-produk.txt').open('x') as stream:
            stream.write('')
    print(f"{args.account} siap lokal: CDP {port}, mode {args.publish_mode or 'manual_review'}, antrean kosong.")
    print('Browser dan penjadwal belum dijalankan.')


if __name__ == '__main__':
    main()
