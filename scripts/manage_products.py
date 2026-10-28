#!/usr/bin/env python3
"""
manage_products.py
CLI buat kelola produk per akun langsung ke database -- gak perlu edit CSV
manual lagi. Nambah produk baru otomatis scan folder video-nya juga.

Usage:
  python3 manage_products.py list <account_slug>
  python3 manage_products.py add <account_slug> "<folder_name>" <tiktok_product_id> [--sort-order N]
  python3 manage_products.py disable <account_slug> "<folder_name>"
  python3 manage_products.py enable <account_slug> "<folder_name>"
  python3 manage_products.py set-id <account_slug> "<folder_name>" <tiktok_product_id>
"""
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path.home() / "tiktok-bot" / "db" / "tiktok_bot.db"
VIDEO_EXTENSIONS = {".mp4", ".mov"}


def get_account_id(cur, slug):
    cur.execute("SELECT id FROM accounts WHERE slug=?", (slug,))
    row = cur.fetchone()
    if not row:
        print(f"GAK KETEMU akun '{slug}'.")
        sys.exit(1)
    return row[0]


def get_content_base(slug):
    return Path.home() / "tiktok-bot" / "content" / slug


def cmd_list(cur, account_slug):
    account_id = get_account_id(cur, account_slug)
    cur.execute(
        "SELECT sort_order, folder_name, tiktok_product_id, active FROM products "
        "WHERE account_id=? ORDER BY sort_order", (account_id,)
    )
    rows = cur.fetchall()
    print(f"{'sort':<6}{'active':<8}{'product_id':<22}folder_name")
    print("-" * 70)
    for sort_order, folder_name, product_id, active in rows:
        status = "ON" if active else "off"
        print(f"{sort_order:<6}{status:<8}{(product_id or '-'):<22}{folder_name}")


def cmd_add(cur, conn, account_slug, folder_name, product_id, sort_order=None):
    account_id = get_account_id(cur, account_slug)
    content_base = get_content_base(account_slug)
    folder_path = content_base / folder_name

    if not folder_path.is_dir():
        print(f"WARNING: folder '{folder_path}' belum ada -- produk tetap diinsert,"
              " tapi contents kosong sampai foldernya diisi video.")

    if sort_order is None:
        cur.execute("SELECT MAX(sort_order) FROM products WHERE account_id=?", (account_id,))
        max_sort = cur.fetchone()[0] or 0
        sort_order = max_sort + 10

    cur.execute("SELECT id FROM products WHERE account_id=? AND folder_name=?", (account_id, folder_name))
    if cur.fetchone():
        print(f"Produk '{folder_name}' udah ada. Pakai 'set-id' kalau mau update ID-nya.")
        sys.exit(1)

    cur.execute(
        "INSERT INTO products (account_id, folder_path, folder_name, sort_order, tiktok_product_id, active) "
        "VALUES (?, ?, ?, ?, ?, 1)",
        (account_id, str(folder_path), folder_name, sort_order, product_id),
    )
    product_row_id = cur.lastrowid
    conn.commit()
    print(f"Produk '{folder_name}' ditambahin (sort_order={sort_order}, id={product_row_id}).")

    if folder_path.is_dir():
        videos = sorted(p for p in folder_path.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS)
        for v in videos:
            cur.execute("INSERT INTO contents (product_id, file_path, status) VALUES (?, ?, 'unused')",
                        (product_row_id, str(v)))
        conn.commit()
        print(f"Scan folder: {len(videos)} video ditemukan & masuk queue.")


def cmd_toggle(cur, conn, account_slug, folder_name, new_active):
    account_id = get_account_id(cur, account_slug)
    cur.execute("SELECT id FROM products WHERE account_id=? AND folder_name=?", (account_id, folder_name))
    row = cur.fetchone()
    if not row:
        print(f"GAK KETEMU produk '{folder_name}' di akun '{account_slug}'.")
        sys.exit(1)
    cur.execute("UPDATE products SET active=? WHERE id=?", (new_active, row[0]))
    conn.commit()
    print(f"Produk '{folder_name}' -> active={new_active}")


def cmd_set_id(cur, conn, account_slug, folder_name, product_id):
    account_id = get_account_id(cur, account_slug)
    cur.execute("SELECT id FROM products WHERE account_id=? AND folder_name=?", (account_id, folder_name))
    row = cur.fetchone()
    if not row:
        print(f"GAK KETEMU produk '{folder_name}'.")
        sys.exit(1)
    cur.execute("UPDATE products SET tiktok_product_id=? WHERE id=?", (product_id, row[0]))
    conn.commit()
    print(f"Produk '{folder_name}' -> tiktok_product_id={product_id}")


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    action = sys.argv[1]
    account_slug = sys.argv[2]

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    if action == "list":
        cmd_list(cur, account_slug)
    elif action == "add":
        if len(sys.argv) < 5:
            print('Usage: manage_products.py add <account> "<folder_name>" <product_id> [--sort-order N]')
            sys.exit(1)
        folder_name, product_id = sys.argv[3], sys.argv[4]
        sort_order = int(sys.argv[sys.argv.index("--sort-order") + 1]) if "--sort-order" in sys.argv else None
        cmd_add(cur, conn, account_slug, folder_name, product_id, sort_order)
    elif action == "disable":
        cmd_toggle(cur, conn, account_slug, sys.argv[3], 0)
    elif action == "enable":
        cmd_toggle(cur, conn, account_slug, sys.argv[3], 1)
    elif action == "set-id":
        cmd_set_id(cur, conn, account_slug, sys.argv[3], sys.argv[4])
    else:
        print(f"Aksi gak dikenal: {action}")
        print(__doc__)
        sys.exit(1)

    conn.close()


if __name__ == "__main__":
    main()
