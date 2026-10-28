#!/usr/bin/env python3
"""
Scan folder tiap produk (dari tabel `products`), cari file video (.mp4/.mov),
insert ke tabel `contents` kalau belum ada. Aman dijalanin berkali-kali --
file yang udah tercatat (dan udah punya status used/unused) gak direset,
cuma file baru yang ditambahin.

Usage: python3 scan_contents.py <account_slug>
Contoh: python3 scan_contents.py akun1
"""

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path.home() / "tiktok-bot" / "db" / "tiktok_bot.db"
VIDEO_EXTENSIONS = {".mp4", ".mov"}


def main():
    if len(sys.argv) != 2:
        print("Usage: python3 scan_contents.py <account_slug>")
        sys.exit(1)

    account_slug = sys.argv[1]
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("SELECT id FROM accounts WHERE slug = ?", (account_slug,))
    row = cur.fetchone()
    if not row:
        print(f"GAK KETEMU akun '{account_slug}'.")
        sys.exit(1)
    account_id = row[0]

    cur.execute(
        "SELECT id, folder_name, folder_path FROM products WHERE account_id = ?",
        (account_id,),
    )
    products = cur.fetchall()

    total_new = 0
    for product_id, folder_name, folder_path in products:
        folder = Path(folder_path)
        if not folder.is_dir():
            print(f"[SKIP] Folder gak ketemu: {folder_path} (produk: {folder_name})")
            continue

        video_files = sorted(
            p for p in folder.iterdir()
            if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
        )

        new_for_product = 0
        for video_path in video_files:
            cur.execute(
                "SELECT id FROM contents WHERE product_id = ? AND file_path = ?",
                (product_id, str(video_path)),
            )
            if cur.fetchone():
                continue
            cur.execute(
                "INSERT INTO contents (product_id, file_path, status) VALUES (?, ?, 'unused')",
                (product_id, str(video_path)),
            )
            new_for_product += 1

        total_new += new_for_product
        print(f"{folder_name}: {len(video_files)} video ditemukan, {new_for_product} baru ditambahin")

    conn.commit()
    conn.close()
    print(f"\nSelesai. Total content baru ditambahin: {total_new}")


if __name__ == "__main__":
    main()
