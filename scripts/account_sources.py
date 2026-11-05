"""Folder pribadi datar dan rotasi ID produk yang persisten."""
from pathlib import Path
import re

DDL = '''
CREATE TABLE IF NOT EXISTS account_sources (
 account_id INTEGER PRIMARY KEY REFERENCES accounts(id), folder_path TEXT UNIQUE NOT NULL,
 next_index INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS content_assignments (
 content_id INTEGER PRIMARY KEY REFERENCES contents(id), product_id INTEGER NOT NULL REFERENCES products(id),
 tiktok_product_id TEXT NOT NULL);
'''


def source(conn, account_id):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='account_sources'").fetchone():
        return None
    return conn.execute('SELECT folder_path,next_index FROM account_sources WHERE account_id=?', (account_id,)).fetchone()


def read_ids(folder):
    path = Path(folder) / 'ID-produk.txt'
    if path.is_symlink():
        raise ValueError('ID-produk.txt tidak boleh symlink')
    try:
        lines = path.read_text(encoding='utf-8-sig').splitlines()
    except FileNotFoundError:
        raise ValueError(f'Isi {path}: satu ID produk per baris') from None
    ids = [line.strip() for line in lines if line.strip()]
    if not ids or any(not re.fullmatch(r'[0-9]{5,30}', value) for value in ids):
        raise ValueError('ID-produk.txt harus berisi satu ID angka 5–30 digit per baris, tanpa judul')
    if len(ids) != len(set(ids)):
        raise ValueError('ID-produk.txt berisi ID duplikat')
    return ids


def sync_products(conn, account_id):
    row = source(conn, account_id)
    ids = read_ids(row[0])
    # Caller owns transaction. Old product/history rows are retained.
    conn.execute('UPDATE products SET active=0 WHERE account_id=?', (account_id,))
    result = []
    for index, value in enumerate(ids):
        name = f'ID {value}'
        conn.execute('INSERT INTO products(account_id,folder_path,folder_name,sort_order,tiktok_product_id) VALUES(?,?,?,?,?) ON CONFLICT(account_id,folder_name) DO UPDATE SET active=1,folder_path=excluded.folder_path,sort_order=excluded.sort_order,tiktok_product_id=excluded.tiktok_product_id', (account_id,row[0],name,index,value))
        result.append(conn.execute('SELECT id FROM products WHERE account_id=? AND folder_name=?',(account_id,name)).fetchone()[0])
    return result


def assign(conn, account_id, content_id):
    old = conn.execute('SELECT product_id,tiktok_product_id FROM content_assignments WHERE content_id=?',(content_id,)).fetchone()
    if old:
        return old
    row = source(conn, account_id)
    ids = read_ids(row[0])
    value = ids[row[1] % len(ids)]
    product = conn.execute('SELECT id FROM products WHERE account_id=? AND tiktok_product_id=? AND active=1 ORDER BY id DESC',(account_id,value)).fetchone()
    if not product:
        raise ValueError('Daftar ID berubah setelah scan; jalankan proses ulang')
    conn.execute('INSERT INTO content_assignments VALUES(?,?,?)',(content_id,product[0],value))
    conn.execute('UPDATE account_sources SET next_index=next_index+1 WHERE account_id=?',(account_id,))
    conn.execute('UPDATE contents SET product_id=? WHERE id=?',(product[0],content_id))
    return product[0], value


def save_ids(folder, text):
    """Validasi sebelum replace atomik; daftar lama utuh bila input salah."""
    import os
    import tempfile
    values = [line.strip() for line in text.splitlines() if line.strip()]
    if not values or any(not re.fullmatch(r'[0-9]{5,30}', value) for value in values) or len(values) != len(set(values)):
        raise ValueError('Isi ID unik, satu ID angka 5–30 digit per baris')
    target = Path(folder) / 'ID-produk.txt'
    if target.is_symlink():
        raise ValueError('ID-produk.txt tidak boleh symlink')
    fd, name = tempfile.mkstemp(prefix='.ID-produk-', dir=folder)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(''.join(value + '\n' for value in values))
            stream.flush(); os.fsync(stream.fileno())
        os.replace(name, target)
    finally:
        if os.path.exists(name): os.unlink(name)
