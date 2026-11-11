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


def directory(name, port):
    from bot_settings import effective_settings
    target = resolve(name, port)
    account = name
    db = BASE / 'db/tiktok_bot.db'
    if db.is_file():
        with sqlite3.connect(db.as_uri()+'?mode=ro', uri=True) as conn:
            row = conn.execute('SELECT slug FROM accounts WHERE chrome_profile_path=?', (str(target),)).fetchone()
            if row:
                account = row[0]
    selected = effective_settings(BASE, account)['chrome_profile_directory']
    if selected != 'Default' and not (target / selected).is_dir():
        raise ValueError('Profil internal yang dikunci tidak ditemukan; tidak membuat profil baru.')
    return selected


if __name__ == '__main__':
    try:
        print(directory(sys.argv[1], int(sys.argv[2])) if '--directory' in sys.argv[3:] else resolve(sys.argv[1], int(sys.argv[2])))
    except (ValueError, sqlite3.Error) as error:
        raise SystemExit(str(error))
