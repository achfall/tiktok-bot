#!/usr/bin/env python3
"""Relokasi path saat pindah device; default preview, --apply membuat backup dulu.
Tidak mengubah akun, produk, status konten, atau riwayat.
"""
import argparse
from datetime import datetime
from pathlib import Path
import sqlite3

BASE = Path.home() / 'tiktok-bot'
COLUMNS = [('accounts', 'chrome_profile_path'), ('content_groups', 'folder_path'), ('products', 'folder_path'), ('contents', 'source_folder'), ('contents', 'file_path')]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--old-root', required=True, help='Root absolut project di Linux')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    old = Path(args.old_root)
    if not old.is_absolute() or old == Path('/') or old == BASE:
        parser.error('old-root harus root absolut project lama yang berbeda')
    db = BASE / 'db/tiktok_bot.db'
    with sqlite3.connect(db.as_uri() + '?mode=ro', uri=True) as conn:
        changes = []
        for table, column in COLUMNS:
            if column not in {row[1] for row in conn.execute(f'PRAGMA table_info({table})')}:
                continue
            for row_id, value in conn.execute(f'SELECT id, {column} FROM {table}'):
                if value is None:
                    continue
                try:
                    relative = Path(value).relative_to(old)
                except ValueError:
                    continue
                target = BASE / relative
                if '..' in relative.parts:
                    raise SystemExit('Path traversal ditolak')
                if not target.exists():
                    raise SystemExit(f'Transfer belum lengkap: {target}')
                changes.append((table, column, row_id, value, str(target)))
        print(f'{len(changes)} path akan direlokasi; status/riwayat tetap dipertahankan.')
        if not args.apply or not changes:
            print('Preview saja. Gunakan --apply setelah bot Linux dan Mac dihentikan.')
            return
        backup = db.with_name(f'{db.name}.backup-{datetime.now():%Y%m%d-%H%M%S-%f}')
        with sqlite3.connect(backup) as destination:
            conn.backup(destination)
    with sqlite3.connect(db.as_uri() + '?mode=rw', uri=True) as conn:
        for table, column, row_id, original, target in changes:
            updated = conn.execute(f'UPDATE {table} SET {column}=? WHERE id=? AND {column}=?', (target, row_id, original))
            if updated.rowcount != 1:
                raise RuntimeError('Database berubah saat relokasi; transaksi dibatalkan')
    print(f'Selesai. Backup: {backup}')


if __name__ == '__main__':
    main()
