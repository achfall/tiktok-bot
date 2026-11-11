#!/usr/bin/env python3
"""Konfigurasi konsep dan anggota akun. Tidak membuka Chrome/upload/jadwal."""
import argparse
from datetime import datetime
from pathlib import Path
import re
import sqlite3
from content_queue import migrate, transaction

from project_paths import PROJECT_ROOT

BASE = PROJECT_ROOT


def backup(db):
    target = db.with_name(f'{db.name}.backup-concepts-{datetime.now():%Y%m%d-%H%M%S-%f}')
    with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
    return target


def assign(conn, account, name, base, move_existing=False):
    moves = []
    try:
        with transaction(conn):
            a = conn.execute('SELECT id,content_group_id FROM accounts WHERE slug=?', (account,)).fetchone()
            g = conn.execute('SELECT id,folder_path FROM content_groups WHERE name=?', (name,)).fetchone()
            if not a or not g:
                raise ValueError('Akun/konsep belum dibuat')
            aid, prior = a
            from account_sources import source
            if source(conn, aid):
                raise ValueError("Akun memakai folder pribadi; tidak bisa digabung ke konsep lewat operasi ini")
            gid, root = g
            if prior == gid:
                return
            if conn.execute("SELECT 1 FROM contents WHERE claimed_by=? AND status IN ('claimed','submitting','uncertain')", (aid,)).fetchone():
                raise ValueError('Akun masih memiliki video terkunci')
            products = conn.execute('SELECT id,folder_name,folder_path FROM products WHERE account_id=?', (aid,)).fetchall()
            for pid, folder_name, old_path in products:
                if not folder_name or Path(folder_name).name != folder_name or folder_name in ('.','..'):
                    raise ValueError('Nama folder produk tidak valid')
                old = Path(old_path)
                new = Path(root) / folder_name
                if prior is None:
                    rows = conn.execute('SELECT id,file_path,status FROM contents WHERE product_id=?', (pid,)).fetchall()
                    if (rows or old.exists()) and not move_existing:
                        raise ValueError('Gunakan --move-existing untuk migrasi folder akun lama')
                    if rows and any(status not in ('used','unused') for _,_,status in rows):
                        raise ValueError('Konten akun lama masih terkunci')
                    if old.resolve() != new.resolve():
                        if new.exists():
                            raise ValueError(f'Tujuan sudah ada; tidak digabung/ditimpa: {new}')
                        if old.is_symlink():
                            raise ValueError('Sumber tidak boleh symlink')
                        if old.exists():
                            old.rename(new)
                            moves.append((old,new))
                        else:
                            new.mkdir(parents=True,exist_ok=True)
                    for cid,file,status in rows:
                        relative = Path(file).relative_to(old)
                        conn.execute('UPDATE contents SET source_folder=?,file_path=? WHERE id=?', (str(new),str(new/relative),cid))
                        if status == 'used':
                            conn.execute("UPDATE contents SET used_by=COALESCE(used_by,(SELECT account_id FROM posts_log WHERE content_id=? AND status IN ('success','draft','scheduled') ORDER BY id DESC LIMIT 1)) WHERE id=?", (cid,cid))
                else:
                    # Pindah anggota grup tidak memindahkan stok/arsip grup lama.
                    new.mkdir(parents=True,exist_ok=True)
                conn.execute('UPDATE products SET folder_path=? WHERE id=?', (str(new),pid))
            conn.execute('UPDATE accounts SET content_group_id=? WHERE id=?', (gid,aid))
    except BaseException:
        for old,new in reversed(moves):
            if new.exists() and not old.exists():
                new.rename(old)
        raise


def rename_concept(conn, old_name, new_name, base):
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*', new_name):
        raise ValueError('Nama konsep tidak valid')
    old = new = None
    moved = False
    try:
        with transaction(conn):
            row = conn.execute('SELECT id,folder_path FROM content_groups WHERE name=?', (old_name,)).fetchone()
            if not row:
                raise ValueError('Konsep sumber tidak ditemukan')
            gid, old_path = row
            old = Path(old_path)
            new = base / 'content' / new_name
            if old_name == new_name:
                return
            if conn.execute('SELECT 1 FROM content_groups WHERE name=? OR folder_path=?', (new_name,str(new))).fetchone() or new.exists():
                raise ValueError('Nama/folder tujuan sudah digunakan; tidak ditimpa')
            if old.is_symlink() or old.resolve().parent != (base/'content').resolve() or not old.is_dir():
                raise ValueError('Folder sumber tidak valid')
            if conn.execute("SELECT 1 FROM contents WHERE status IN ('claimed','submitting','uncertain') AND (source_folder=? OR source_folder LIKE ? OR claimed_by IN (SELECT id FROM accounts WHERE content_group_id=?))", (str(old),str(old)+'/%',gid)).fetchone():
                raise ValueError('Konsep masih memiliki klaim tertahan; hentikan proses dan periksa antrean')
            changes = []
            for table,column in [('products','folder_path'),('contents','file_path'),('contents','source_folder')]:
                for row_id,value in conn.execute(f'SELECT id,{column} FROM {table}').fetchall():
                    if value is None:
                        continue
                    try:
                        relative = Path(value).relative_to(old)
                    except ValueError:
                        continue
                    if '..' in relative.parts:
                        raise ValueError('Path tidak valid')
                    changes.append((table,column,row_id,str(new/relative)))
            old.rename(new)
            moved = True
            conn.execute('UPDATE content_groups SET name=?,folder_path=? WHERE id=?', (new_name,str(new),gid))
            for table,column,row_id,value in changes:
                conn.execute(f'UPDATE {table} SET {column}=? WHERE id=?', (value,row_id))
    except BaseException:
        if moved and new.exists() and not old.exists():
            new.rename(old)
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='action',required=True)
    sub.add_parser('migrate')
    create=sub.add_parser('create');create.add_argument('concept')
    join=sub.add_parser('assign');join.add_argument('account');join.add_argument('concept');join.add_argument('--move-existing',action='store_true')
    rename=sub.add_parser('rename');rename.add_argument('old_name');rename.add_argument('new_name')
    sub.add_parser('list')
    args=parser.parse_args()
    db=BASE/'db/tiktok_bot.db'
    if not db.is_file():raise SystemExit('Database belum ada; buat akun dahulu')
    if args.action=='migrate':
        print(f'Backup: {backup(db)}')
        with sqlite3.connect(db) as conn:migrate(conn)
        print('Schema konsep/antrean siap.')
        return
    if args.action in ('assign','rename'):print(f'Backup: {backup(db)}')
    with sqlite3.connect(db,timeout=30) as conn:
        if args.action=='create':
            if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*',args.concept):raise SystemExit('Nama konsep tidak valid')
            folder=BASE/'content'/args.concept
            with transaction(conn):
                existing=conn.execute('SELECT folder_path FROM content_groups WHERE name=?',(args.concept,)).fetchone()
                if not existing:
                    if folder.exists():raise SystemExit('Folder sudah ada dan belum terdaftar; tidak digunakan otomatis')
                    folder.mkdir(parents=True)
                    conn.execute('INSERT INTO content_groups (name,folder_path) VALUES (?,?)',(args.concept,str(folder)))
            print(f'Konsep {args.concept}: {folder}')
        elif args.action=='rename':
            rename_concept(conn,args.old_name,args.new_name,BASE)
            print(f'Konsep dan seluruh path aktif: {args.old_name} -> {args.new_name}')
        elif args.action=='assign':
            assign(conn,args.account,args.concept,BASE,args.move_existing)
            print(f'{args.account} mengambil konten dari {args.concept}. Jadwal tidak diaktifkan.')
        else:
            for row in conn.execute('SELECT a.slug,g.name,g.folder_path FROM accounts a LEFT JOIN content_groups g ON g.id=a.content_group_id ORDER BY a.id'):
                print(' | '.join(str(v or '(folder pribadi)') for v in row))


if __name__=='__main__':main()
