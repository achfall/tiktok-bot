"""Satu lokasi data untuk CLI, worker dan panel; bisa diisolasi saat pengujian."""
import os
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(os.environ.get('TIKTOK_BOT_HOME', str(CODE_ROOT))).expanduser().resolve()
