#!/usr/bin/env python3
"""Arsipkan video used yang punya hasil success/draft; tidak hapus otomatis."""
import argparse
import os
from pathlib import Path
import sqlite3

ARCHIVE_DIR = 'sudah-dipakai'
from project_paths import PROJECT_ROOT

DB_PATH = PROJECT_ROOT / 'db/tiktok_bot.db'


def archive_content(conn, content_id):
    """Status used dan hasil posting harus sudah di-commit sebelum dipanggil.

    Kegagalan arsip tidak mengembalikan video ke antrean. File tujuan tidak
    ditimpa; perubahan filesystem dibalik jika update database gagal.
    """
    if conn.in_transaction:
        raise RuntimeError('Commit hasil posting sebelum mengarsipkan video')
    conn.execute('BEGIN IMMEDIATE')
    destination = None
    source = None
    linked = False
    try:
        row = conn.execute(
            'SELECT c.file_path,COALESCE(c.source_folder,p.folder_path),c.status,c.source_folder,c.used_by FROM contents c '
            'JOIN products p ON p.id=c.product_id WHERE c.id=?', (content_id,),
        ).fetchone()
        if not row or row[2] != 'used':
            raise ValueError('Hanya video used yang boleh diarsipkan')
        if not conn.execute("SELECT 1 FROM posts_log WHERE content_id=? AND status IN ('success','draft')", (content_id,)).fetchone():
            raise ValueError('Hasil publish/draft terverifikasi belum tercatat')
        source = Path(row[0])
        product = Path(row[1])
        archive = product / ARCHIVE_DIR
        flat = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='account_sources'").fetchone()
        flat = flat and conn.execute('SELECT 1 FROM account_sources WHERE folder_path=?',(str(product),)).fetchone()
        if row[3] and not flat:
            owner = conn.execute(
                "SELECT a.chrome_profile_path FROM accounts a WHERE a.id=COALESCE(?,(SELECT account_id FROM posts_log WHERE content_id=? AND status IN ('success','draft') ORDER BY id DESC LIMIT 1))",
                (row[4], content_id),
            ).fetchone()
            if not owner:
                raise ValueError('Akun pemakai video belum tercatat')
            account_name = Path(owner[0]).name
            if not account_name or account_name in ('.','..'):
                raise ValueError('Nama arsip akun tidak valid')
            if archive.is_symlink():
                raise ValueError('Folder arsip tidak boleh berupa symlink')
            archive = archive / account_name
        if source.parent == archive or (flat and archive in source.parents):
            conn.rollback()
            # Boleh sudah dihapus manual oleh operator; tidak dibuat ulang.
            return source
        if source.is_symlink() or source.parent.resolve() not in (product.resolve(), (product / ARCHIVE_DIR).resolve()) or not source.is_file():
            raise ValueError('File sumber tidak tersedia/langsung dalam folder produk')
        if archive.is_symlink():
            raise ValueError('Folder arsip tidak boleh berupa symlink')
        archive.mkdir(parents=True, exist_ok=True)
        # link + unlink dalam filesystem yang sama mencegah overwrite tujuan.
        for index in range(1000):
            suffix = '' if index == 0 else f'-content-{content_id}-{index}'
            candidate = archive / f'{source.stem}{suffix}{source.suffix}'
            if conn.execute('SELECT 1 FROM contents WHERE file_path=?', (str(candidate),)).fetchone():
                continue
            try:
                os.link(source, candidate)
            except FileExistsError:
                continue
            destination = candidate
            linked = True
            break
        if destination is None:
            raise FileExistsError('Tidak ada nama arsip yang tersedia')
        source.unlink()
        conn.execute('UPDATE contents SET file_path=? WHERE id=?', (str(destination), content_id))
        conn.commit()
        return destination
    except Exception:
        conn.rollback()
        if linked and destination is not None:
            if not source.exists():
                os.link(destination, source)
            destination.unlink()
        raise


def archive_after_success(conn, content_id):
    """Jangan retry upload jika operasi arsip lokal gagal."""
    try:
        path = archive_content(conn, content_id)
        print(f'  Arsip video: {path}')
        return True
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
        print(f'  PERINGATAN: video tetap used, tetapi arsip belum selesai: {error}')
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('account')
    args = parser.parse_args()
    with sqlite3.connect(DB_PATH.as_uri()+'?mode=rw', uri=True) as conn:
        rows = conn.execute(
            "SELECT c.id FROM contents c JOIN products p ON p.id=c.product_id "
            "JOIN accounts a ON a.id=COALESCE(c.used_by,p.account_id) WHERE a.slug=? AND c.status='used' "
            "AND EXISTS (SELECT 1 FROM posts_log l WHERE l.content_id=c.id AND l.status IN ('success','draft'))",
            (args.account,),
        ).fetchall()
        failures = sum(not archive_after_success(conn, row[0]) for row in rows)
    if failures:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
