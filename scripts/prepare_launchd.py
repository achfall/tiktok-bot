#!/usr/bin/env python3
"""Buat plist inert dari akun DB; tidak install/load atau mengaktifkan posting."""
import plistlib
import re
import sqlite3
from pathlib import Path

BASE = Path.home() / "tiktok-bot"


def main():
    db = BASE / "db/tiktok_bot.db"
    if not db.is_file():
        raise SystemExit("Database belum tersedia; transfer database Linux dahulu.")
    with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True) as conn:
        accounts = conn.execute("SELECT slug, cdp_port FROM accounts WHERE active=1").fetchall()
    output = BASE / "config/launchd"
    output.mkdir(parents=True, exist_ok=True)
    (BASE / "logs").mkdir(exist_ok=True)
    for slug, port in accounts:
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", slug) or not isinstance(port, int) or not 1024 <= port <= 65535:
            raise SystemExit("Akun/port tidak valid")
        label = f"local.tiktokbot.{slug}"
        target = output / f"{label}.plist"
        config = {
            "Label": label,
            "ProgramArguments": ["/bin/bash", str(BASE / "scripts/launchd_runner.sh"), slug, str(port)],
            "WorkingDirectory": str(BASE),
            "StartCalendarInterval": {"Hour": 10, "Minute": 0},
            "RunAtLoad": False,
            "KeepAlive": False,
            "ProcessType": "Interactive",
            "StandardOutPath": str(BASE / f"logs/{slug}-service.log"),
            "StandardErrorPath": str(BASE / f"logs/{slug}-service.log"),
        }
        try:
            with target.open("xb") as stream:
                plistlib.dump(config, stream)
        except FileExistsError:
            print(f"Dipertahankan: {target}")
        else:
            print(f"Disiapkan (belum aktif): {target}")


if __name__ == "__main__":
    main()
