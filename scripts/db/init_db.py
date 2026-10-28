#!/usr/bin/env python3
"""Bikin/update file database SQLite sesuai schema.sql -- aman dijalanin berkali-kali."""
import sqlite3
from pathlib import Path

DB_PATH = Path.home() / "tiktok-bot" / "db" / "tiktok_bot.db"
SCHEMA_PATH = Path.home() / "tiktok-bot" / "scripts" / "db" / "schema.sql"

def main():
    conn = sqlite3.connect(DB_PATH)
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()
    print(f"Database siap di: {DB_PATH}")

if __name__ == "__main__":
    main()
