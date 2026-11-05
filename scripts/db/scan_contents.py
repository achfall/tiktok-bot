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
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from content_queue import scan_account

from project_paths import PROJECT_ROOT

DB_PATH = PROJECT_ROOT / "db/tiktok_bot.db"
VIDEO_EXTENSIONS = {".mp4", ".mov"}


def main():
    if len(sys.argv) != 2:
        print("Usage: python3 scan_contents.py <account_slug>")
        sys.exit(1)

    account_slug = sys.argv[1]
    conn = sqlite3.connect(DB_PATH, timeout=30)
    cur = conn.cursor()

    cur.execute("SELECT id FROM accounts WHERE slug = ?", (account_slug,))
    row = cur.fetchone()
    if not row:
        print(f"GAK KETEMU akun '{account_slug}'.")
        sys.exit(1)
    account_id = row[0]

    total_new = scan_account(conn, account_id)
    conn.close()
    print(f"Selesai. Total video baru: {total_new}; arsip tidak dipindai.")


if __name__ == "__main__":
    main()
