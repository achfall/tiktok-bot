#!/usr/bin/env python3
"""Buat LaunchAgent panel; pemasangan/aktivasi dilakukan eksplisit oleh operator."""
import argparse
from pathlib import Path
import plistlib

LABEL = 'local.ruangkonten.panel'


def panel_agent(root):
    root = Path(root).resolve()
    return {
        'Label': LABEL,
        'ProgramArguments': [str(root/'venv/bin/python'), str(root/'scripts/start_panel.py')],
        'WorkingDirectory': str(root),
        'EnvironmentVariables': {'TIKTOK_BOT_HOME': str(root), 'PYTHONUNBUFFERED': '1'},
        'RunAtLoad': True,
        'KeepAlive': True,
        'ThrottleInterval': 10,
        'ProcessType': 'Interactive',
        'StandardOutPath': str(root/'logs/panel-service.log'),
        'StandardErrorPath': str(root/'logs/panel-service.log'),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    for name in ('venv/bin/python', 'scripts/start_panel.py'):
        if not (root/name).is_file():
            parser.error(f'Tidak ditemukan: {root/name}')
    output = args.output or root/'config/launchd'/f'{LABEL}.plist'
    output.parent.mkdir(parents=True, exist_ok=True)
    (root/'logs').mkdir(exist_ok=True)
    output.write_bytes(plistlib.dumps(panel_agent(root), sort_keys=False))
    print(f'Plist panel dibuat: {output}; belum diaktifkan.')


if __name__ == '__main__':
    main()
