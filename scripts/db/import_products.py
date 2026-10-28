#!/usr/bin/env python3
"""
Import CSV mapping produk ke tabel `products`. Upsert by (account_id, folder_name)
-- aman dijalanin berkali-kali, baris yang folder_name-nya udah ada bakal di-update,
bukan dobel.

Usage: python3 import_products.py <account_slug> <csv_path>
Contoh: python3 import_products.py akun1 ~/tiktok-bot/config/akun1_products.csv
"""

import csv
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path.home() / "tiktok-bot" / "db" / "tiktok_bot.db"


def main():
    if len(sys.argv) != 3:
        print("Usage: python3 import_products.py <account_slug> <csv_path>")
        sys.exit(1)

    account_slug, csv_path = sys.argv[1], sys.argv[2]
    content_base = Path.home() / "tiktok-bot" / "content" / account_slug

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("SELECT id FROM accounts WHERE slug = ?", (account_slug,))
    row = cur.fetchone()
    if not row:
        print(f"GAK KETEMU akun dengan slug '{account_slug}' di tabel accounts. Insert dulu.")
        sys.exit(1)
    account_id = row[0]

    inserted, updated = 0, 0
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            folder_name = row["folder_name"].strip()
            sort_order = int(row["sort_order"])
            product_id = row["tiktok_product_id"].strip() or None
            active = int(row["active"])
            folder_path = str(content_base / folder_name)

            cur.execute(
                "SELECT id FROM products WHERE account_id = ? AND folder_name = ?",
                (account_id, folder_name),
            )
            existing = cur.fetchone()

            if existing:
                cur.execute(
                    """UPDATE products SET folder_path=?, sort_order=?,
                       tiktok_product_id=?, active=? WHERE id=?""",
                    (folder_path, sort_order, product_id, active, existing[0]),
                )
                updated += 1
            else:
                cur.execute(
                    """INSERT INTO products (account_id, folder_path, folder_name,
                       sort_order, tiktok_product_id, active)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (account_id, folder_path, folder_name, sort_order, product_id, active),
                )
                inserted += 1

    conn.commit()
    conn.close()
    print(f"Selesai. Insert baru: {inserted}, Update: {updated}")


if __name__ == "__main__":
    main()
