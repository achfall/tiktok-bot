"""Batas file bersama panel dan orchestrator sebelum membuka Chrome."""
from pathlib import Path

MAX_VIDEO_BYTES = 2 * 1024 * 1024 * 1024


def validate_video(path):
    path=Path(path)
    if path.is_symlink() or not path.is_file() or path.suffix.lower() not in ('.mp4','.mov'):
        raise ValueError('File video MP4/MOV tidak tersedia atau berupa symlink.')
    if not 0 < path.stat().st_size <= MAX_VIDEO_BYTES:
        raise ValueError('Ukuran video harus 1 byte–2 GB; Chrome belum dibuka.')
