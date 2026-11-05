#!/usr/bin/env python3
"""Resolve ID internal/nama folder ke profil di DB; tidak membuat profil."""
from pathlib import Path
import sqlite3
import sys

from project_paths import PROJECT_ROOT

BASE = PROJECT_ROOT


def resolve(name, port):
    root = BASE / 'chrome-profiles'
    db = BASE / 'db/tiktok_bot.db'
    if db.is_file():
        with sqlite3.connect(db.as_uri()+'?mode=ro', uri=True) as conn:
            rows = conn.execute('SELECT slug,chrome_profile_path,cdp_port FROM accounts').fetchall()
        matches = [row for row in rows if row[0] == name or Path(row[1]).name == name]
        if len(matches) > 1:
            raise ValueError('Nama profil ambigu')
        if matches:
            _, path, expected = matches[0]
            if expected != port:
                raise ValueError(f'Port profil harus {expected}')
            target = Path(path)
            if not target.is_absolute() or target.resolve().parent != root.resolve():
                raise ValueError('Path profil DB harus berada langsung dalam chrome-profiles')
            return target
    return root / name


if __name__ == '__main__':
    try:
        print(resolve(sys.argv[1], int(sys.argv[2])))
    except (ValueError, sqlite3.Error) as error:
        raise SystemExit(str(error))
