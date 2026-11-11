"""Antrean bersama per folder konsep, dengan klaim SQLite yang persisten.

Klaim tidak kedaluwarsa otomatis: proses mati/hasil submit ambigu harus diperiksa
operator agar video tidak dikirim ulang oleh akun lain.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import os
import sqlite3
import uuid
from account_sources import source as account_source, sync_products, assign

VIDEO_EXTENSIONS = {'.mp4', '.mov'}


@contextmanager
def transaction(conn):
    if conn.in_transaction:
        raise RuntimeError('Commit transaksi sebelumnya dahulu')
    conn.execute('BEGIN IMMEDIATE')
    try:
        yield
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def migrate(conn):
    """Migrasi aditif; backup database dilakukan CLI sebelum migrasi instalasi."""
    with transaction(conn):
        conn.execute('CREATE TABLE IF NOT EXISTS content_groups (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, folder_path TEXT UNIQUE NOT NULL)')
        columns = {row[1] for row in conn.execute('PRAGMA table_info(accounts)')}
        if 'caption_style' not in columns:
            conn.execute("ALTER TABLE accounts ADD COLUMN caption_style TEXT NOT NULL DEFAULT 'generic'")
        if 'content_group_id' not in columns:
            conn.execute('ALTER TABLE accounts ADD COLUMN content_group_id INTEGER REFERENCES content_groups(id)')
        columns = {row[1] for row in conn.execute('PRAGMA table_info(contents)')}
        for name, declaration in {
            'source_folder': 'TEXT', 'claimed_by': 'INTEGER REFERENCES accounts(id)',
            'claim_product_id': 'INTEGER REFERENCES products(id)', 'claim_token': 'TEXT',
            'claimed_at': 'TEXT', 'used_by': 'INTEGER REFERENCES accounts(id)',
            'submission_log_id': 'INTEGER REFERENCES posts_log(id)',
        }.items():
            if name not in columns:
                conn.execute(f'ALTER TABLE contents ADD COLUMN {name} {declaration}')
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS shared_video_path ON contents(source_folder,file_path) WHERE source_folder IS NOT NULL')
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_active_video_per_account ON contents(claimed_by) WHERE status IN ('claimed','submitting')")
        conn.execute('CREATE INDEX IF NOT EXISTS queue_pick ON contents(source_folder,status,id)')


def group_root(conn, account_id):
    return conn.execute('SELECT g.folder_path FROM accounts a JOIN content_groups g ON g.id=a.content_group_id WHERE a.id=?', (account_id,)).fetchone()


def scan_account(conn, account_id):
    """Satu baris video per sumber meski beberapa akun melakukan scan serentak."""
    flat = account_source(conn, account_id)
    if flat:
        with transaction(conn):
            sync_products(conn, account_id)
    products = conn.execute('SELECT id,folder_path,folder_name FROM products WHERE account_id=? AND active=1', (account_id,)).fetchall()
    if flat:
        products = products[:1]
    group = group_root(conn, account_id)
    added = 0
    for product_id, folder_path, folder_name in products:
        folder = Path(folder_path)
        if group and folder.resolve() != (Path(group[0]) / folder_name).resolve():
            raise ValueError('Folder produk tidak sesuai konsep akun')
        if not folder.is_dir():
            continue
        files = sorted(p for p in folder.iterdir() if p.is_file() and not p.is_symlink() and p.suffix.lower() in VIDEO_EXTENSIONS)
        with transaction(conn):
            for file in files:
                if group or flat:
                    exists = conn.execute('SELECT 1 FROM contents WHERE source_folder=? AND file_path=?', (str(folder), str(file))).fetchone()
                else:
                    exists = conn.execute('SELECT 1 FROM contents WHERE product_id=? AND file_path=?', (product_id, str(file))).fetchone()
                if not exists:
                    conn.execute('INSERT INTO contents (product_id,file_path,status,source_folder) VALUES (?, ?,\'unused\',?)', (product_id,str(file),str(folder) if group or flat else None))
                    added += 1
    return added


@dataclass(frozen=True)
class Claim:
    content_id: int
    product_id: int
    account_id: int
    path: str
    token: str


def claim_video(conn, account_id, product_id, content_id=None):
    with transaction(conn):
        product = conn.execute('SELECT folder_path,folder_name FROM products WHERE id=? AND account_id=? AND active=1', (product_id, account_id)).fetchone()
        if not product:
            raise ValueError('Produk tidak aktif/di luar akun')
        if conn.execute("SELECT 1 FROM contents WHERE claimed_by=? AND status IN ('claimed','submitting','uncertain')", (account_id,)).fetchone():
            raise ValueError('Akun masih punya video terkunci; periksa antrean dahulu')
        group = group_root(conn, account_id)
        if group and Path(product[0]).resolve() != (Path(group[0])/product[1]).resolve():
            raise ValueError('Produk tidak termasuk konsep akun')
        query = "SELECT id,file_path FROM contents WHERE status='unused' AND "
        params = []
        flat = account_source(conn, account_id)
        if flat:
            query += 'source_folder=?'
            params.append(flat[0])
        elif group:
            query += 'source_folder=?'
            params.append(product[0])
        else:
            query += 'product_id=? AND source_folder IS NULL'
            params.append(product_id)
        if content_id is not None:
            query += ' AND id=?'
            params.append(content_id)
        query += ' ORDER BY id'
        for cid, path in conn.execute(query, params).fetchall():
            if Path(path).is_symlink() or not Path(path).is_file():
                continue
            if flat:
                product_id, _ = assign(conn, account_id, cid)
            token = uuid.uuid4().hex
            conn.execute("UPDATE contents SET status='claimed',claimed_by=?,claim_product_id=?,claim_token=?,claimed_at=datetime('now'),submission_log_id=NULL WHERE id=? AND status='unused'", (account_id, product_id, token, cid))
            return Claim(cid, product_id, account_id, path, token)
    return None


def require_claim(conn, claim, statuses):
    row = conn.execute('SELECT status,submission_log_id FROM contents WHERE id=? AND claimed_by=? AND claim_product_id=? AND claim_token=?', (claim.content_id,claim.account_id,claim.product_id,claim.token)).fetchone()
    if not row or row[0] not in statuses:
        raise ValueError('Klaim video tidak cocok atau sudah selesai')
    return row


def daily_post_count(conn, account_id, quota_day=None):
    """Kuota lokal akun: hasil selesai dan submit yang belum pasti ikut dihitung."""
    from schedule_store import attach
    if attach(conn):
        return conn.execute(
            "SELECT COUNT(*) FROM posts_log l LEFT JOIN schedule_meta.submissions s "
            "ON s.log_id=l.id AND s.account_id=l.account_id AND s.content_id=l.content_id WHERE l.account_id=? "
            "AND l.status IN ('success','draft','scheduled','submitting','uncertain') "
            "AND COALESCE(s.quota_day,DATE(l.published_at,'localtime'))=COALESCE(?,DATE('now','localtime'))",
            (account_id,quota_day),
        ).fetchone()[0]
    return conn.execute(
        "SELECT COUNT(*) FROM posts_log WHERE account_id=? "
        "AND status IN ('success','draft','scheduled','submitting','uncertain') "
        "AND DATE(published_at,'localtime')=COALESCE(?,DATE('now','localtime'))",
        (account_id,quota_day),
    ).fetchone()[0]


def begin_submission(conn, claim, caption, daily_limit=None, quota_day=None, scheduled=False, test_scheduled=False):
    from schedule_store import attach
    attach(conn)
    with transaction(conn):
        require_claim(conn, claim, ('claimed',))
        if daily_limit is not None and daily_post_count(conn, claim.account_id, quota_day) >= daily_limit:
            raise ValueError('Batas video hari ini sudah tercapai; submit tidak dilakukan.')
        log_id = conn.execute("INSERT INTO posts_log (account_id,product_id,content_id,caption_text,status,published_at) VALUES (?,?,?,?,'submitting',datetime('now'))", (claim.account_id,claim.product_id,claim.content_id,caption)).lastrowid
        conn.execute("UPDATE contents SET status='submitting',submission_log_id=? WHERE id=?", (log_id,claim.content_id))
        if scheduled:
            if not quota_day:
                raise ValueError('Tanggal kuota batch Schedule belum ditetapkan.')
            condition='batch_id=0' if test_scheduled else 'batch_id IN (SELECT id FROM schedule_meta.batches WHERE quota_day=?)'
            args=(log_id,claim.account_id,claim.content_id) + (() if test_scheduled else (quota_day,))
            changed=conn.execute('UPDATE schedule_meta.slots SET log_id=? WHERE account_id=? AND content_id=? AND log_id IS NULL AND '+condition,args).rowcount
            if changed != 1:
                raise ValueError('Slot Schedule belum disiapkan; submit dibatalkan.')
            conn.execute('INSERT INTO schedule_meta.submissions(log_id,account_id,content_id,target_at,quota_day) '
                         'SELECT ?,account_id,content_id,target_at,? FROM schedule_meta.slots WHERE log_id=?',
                         (log_id,quota_day,log_id))


def finish_success(conn, claim, mode):
    if mode not in ('auto','manual','schedule'):
        raise ValueError('Mode hasil posting tidak valid')
    with transaction(conn):
        _, log_id = require_claim(conn, claim, ('submitting','uncertain'))
        if mode=='schedule':
            if not conn.execute('SELECT 1 FROM schedule_meta.submissions WHERE log_id=? AND account_id=? AND content_id=?', (log_id,claim.account_id,claim.content_id)).fetchone():
                raise ValueError('Metadata jadwal tidak tersedia; hasil harus diperiksa.')
            conn.execute("UPDATE posts_log SET status='scheduled',error_message=NULL WHERE id=?", (log_id,))
        else:
            conn.execute("UPDATE posts_log SET status=?,error_message=NULL,published_at=datetime('now') WHERE id=?", ('success' if mode=='auto' else 'draft',log_id))
        conn.execute("UPDATE contents SET status='used',used_at=datetime('now'),used_by=?,claimed_by=NULL,claim_product_id=NULL,claim_token=NULL,claimed_at=NULL WHERE id=?", (claim.account_id,claim.content_id))


def finish_failure(conn, claim, caption, error):
    with transaction(conn):
        status, log_id = require_claim(conn, claim, ('claimed','submitting','uncertain'))
        if status in ('submitting','uncertain'):
            conn.execute("UPDATE contents SET status='uncertain' WHERE id=?", (claim.content_id,))
            conn.execute("UPDATE posts_log SET status='uncertain',error_message=? WHERE id=?", (error,log_id))
            return 'uncertain'
        conn.execute("INSERT INTO posts_log (account_id,product_id,content_id,caption_text,status,error_message,published_at) VALUES (?,?,?,?,'failed',?,datetime('now'))", (claim.account_id,claim.product_id,claim.content_id,caption,error))
        conn.execute("UPDATE contents SET status='unused',claimed_by=NULL,claim_product_id=NULL,claim_token=NULL,claimed_at=NULL WHERE id=?", (claim.content_id,))
        return 'unused'


def release_preview(conn, claim):
    with transaction(conn):
        require_claim(conn, claim, ('claimed',))
        conn.execute("UPDATE contents SET status='unused',claimed_by=NULL,claim_product_id=NULL,claim_token=NULL,claimed_at=NULL WHERE id=?", (claim.content_id,))


def prepare_retest(conn, account_id, product_id, original_id):
    """Salinan eksplisit untuk tes; baris used dan arsip lama tidak direset."""
    product = conn.execute('SELECT folder_path FROM products WHERE id=? AND account_id=?', (product_id,account_id)).fetchone()
    row = conn.execute('SELECT file_path,source_folder,product_id FROM contents WHERE id=?', (original_id,)).fetchone()
    if not product or not row or not Path(row[0]).is_file():
        raise ValueError('Video tes tidak tersedia')
    group = group_root(conn, account_id)
    flat = account_source(conn, account_id)
    shared_source = bool(group or flat)
    if (shared_source and row[1] != product[0]) or (not shared_source and row[2] != product_id):
        raise ValueError('Video tes bukan milik sumber akun')
    source = Path(row[0])
    target = Path(product[0]) / f'{source.stem}-tes-{uuid.uuid4().hex[:10]}{source.suffix}'
    import shutil
    created = False
    try:
        with source.open('rb') as src, target.open('xb') as dst:
            created = True
            shutil.copyfileobj(src,dst)
        with transaction(conn):
            cid = conn.execute("INSERT INTO contents (product_id,file_path,status,source_folder) VALUES (?,?,'unused',?)", (product_id,str(target),product[0] if shared_source else None)).lastrowid
    except BaseException:
        if created:
            target.unlink(missing_ok=True)
        raise
    return cid


def resume_submission(conn, claim):
    with transaction(conn):
        require_claim(conn, claim, ('uncertain',))
        conn.execute("UPDATE contents SET status='submitting' WHERE id=?", (claim.content_id,))
        conn.execute("UPDATE posts_log SET status='submitting' WHERE id=(SELECT submission_log_id FROM contents WHERE id=?)", (claim.content_id,))
