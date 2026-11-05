#!/usr/bin/env python3
"""Migrasi satu sumber lama ke folder akun datar, dengan backup dan rollback."""
import argparse
from pathlib import Path
import sqlite3
from account_sources import DDL, source, read_ids
from content_queue import transaction
from manage_concepts import backup
from project_paths import PROJECT_ROOT


def convert(conn, slug, base):
    row = conn.execute('SELECT id,chrome_profile_path,content_group_id FROM accounts WHERE slug=?',(slug,)).fetchone()
    if not row:
        raise ValueError('Akun tidak ditemukan')
    aid, profile, group = row
    if source(conn, aid):
        return Path(source(conn, aid)[0])
    target = base / 'content' / Path(profile).name
    products = conn.execute('SELECT id,folder_path,tiktok_product_id FROM products WHERE account_id=? ORDER BY sort_order,id',(aid,)).fetchall()
    roots = {p[1] for p in products}
    if len(roots)>1:
        raise ValueError('Beberapa folder produk lama: perlu pemetaan migrasi terpisah; tidak ada data dipindahkan')
    if group and conn.execute('SELECT COUNT(*) FROM accounts WHERE content_group_id=?',(group,)).fetchone()[0] != 1:
        raise ValueError('Konsep dipakai beberapa akun; pisahkan stok dahulu')
    old = Path(next(iter(roots))) if roots else None
    if target.exists() or target.is_symlink():
        raise ValueError(f'Tujuan sudah ada; tidak ditimpa: {target}')
    if old and (old.is_symlink() or not old.is_dir() or (base/'content').resolve() not in old.resolve().parents):
        raise ValueError('Folder sumber tidak aman/tidak ada')
    ids = list(dict.fromkeys(p[2] for p in products if p[2]))
    created = moved = wrote = False
    try:
        with transaction(conn):
            if conn.execute("SELECT 1 FROM contents WHERE status IN ('claimed','submitting','uncertain') AND (claimed_by=? OR source_folder=? OR product_id IN (SELECT id FROM products WHERE account_id=?))",(aid,str(old),aid)).fetchone():
                raise ValueError('Masih ada klaim tertahan; hentikan proses dan periksa TikTok dahulu')
            rows = conn.execute('SELECT c.id,c.file_path FROM contents c JOIN products p ON p.id=c.product_id WHERE p.account_id=? OR c.source_folder=?',(aid,str(old))).fetchall()
            changes = [(cid, str(target/Path(path).relative_to(old))) for cid,path in rows] if old else []
            if old:
                old.rename(target);moved=True
            else:
                target.mkdir(parents=True);created=True
            txt = target/'ID-produk.txt'
            if not txt.exists():
                with txt.open('x') as stream:stream.write(''.join(value+'\n' for value in ids))
                wrote=True
            if ids:read_ids(target)
            conn.execute('INSERT INTO account_sources(account_id,folder_path) VALUES(?,?)',(aid,str(target)))
            conn.execute('UPDATE accounts SET content_group_id=NULL WHERE id=?',(aid,))
            conn.execute('UPDATE products SET folder_path=? WHERE account_id=?',(str(target),aid))
            for cid,path in changes:
                conn.execute('UPDATE contents SET file_path=?,source_folder=? WHERE id=?',(path,str(target),cid))
        return target
    except BaseException:
        if wrote:(target/'ID-produk.txt').unlink()
        if moved:target.rename(old)
        elif created:target.rmdir()
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('account');args=parser.parse_args()
    db=PROJECT_ROOT/'db/tiktok_bot.db'
    print(f'Backup: {backup(db)}')
    with sqlite3.connect(db,timeout=30) as conn:
        conn.executescript(DDL)
        print(f'Sumber akun: {convert(conn,args.account,PROJECT_ROOT)}')
    print('Riwayat dipertahankan; browser/jadwal tidak dijalankan.')


if __name__=='__main__':main()
