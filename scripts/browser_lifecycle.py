"""Chrome dibuka hanya untuk pekerjaan aktif; slot bersama lintas proses akun."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

CODE_ROOT = Path(__file__).resolve().parents[1]
OWNED_PROCESSES = {}


def browser_ready(port):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/version', timeout=2) as response:
            return response.status == 200
    except OSError:
        return False


def prepare_browser(base, account, port):
    from bot_settings import effective_settings
    selected = effective_settings(base, account)['chrome_profile_directory']
    already_open = browser_ready(port)
    if already_open and selected == 'Default':
        return False
    print(f'Membuka Chrome khusus akun dengan profil internal {selected}.', flush=True)
    result = subprocess.run(
        ['bash', str(CODE_ROOT/'scripts/start-chrome-profile.sh'), account, str(port)],
        cwd=CODE_ROOT,
        env={**os.environ, 'TIKTOK_BOT_HOME': str(base), 'TIKTOK_BOT_PYTHON': sys.executable},
        timeout=10,
    )
    if result.returncode:
        raise RuntimeError('Launcher Chrome gagal; tidak ada upload dilakukan.')
    for _ in range(30):
        if browser_ready(port):
            if not already_open:
                from browser_process import capture_process
                identity=capture_process(port)
                if identity is not None:
                    OWNED_PROCESSES[port]=identity
                else:
                    print('Identitas proses Chrome belum dapat direkam; Quit hanya lewat CDP.',flush=True)
            return not already_open
        time.sleep(1)
    raise RuntimeError('Chrome CDP belum siap setelah 30 detik; tidak ada upload dilakukan.')


@contextmanager
def browser_slot(base, account, port, maximum):
    """Hitung hanya lease proses bot aktif, bukan Chrome yang masih merespons CDP.

    Metadata tanpa flock tidak mengisi kuota. File lock tidak dihapus: menghapusnya
    bisa membuat dua proses mengunci inode berbeda untuk slot yang sama.
    """
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 1:
        raise ValueError('Batas Chrome bot harus bilangan bulat positif.')
    root = Path(base)/'db/browser-slots'
    root.mkdir(parents=True, exist_ok=True)
    notice = False
    selected = None
    try:
        while selected is None:
            # Serialisasi penerimaan agar perubahan batas juga memperhitungkan
            # worker lama yang masih mengunci slot dengan indeks lebih tinggi.
            with (root/'.allocation.lock').open('a+') as admission:
                fcntl.flock(admission, fcntl.LOCK_EX)
                streams = []
                try:
                    paths = {root/f'{index}.json' for index in range(maximum)}
                    paths.update(path for path in root.glob('*.json') if path.stem.isdecimal())
                    busy = 0
                    for path in sorted(paths, key=lambda path: int(path.stem)):
                        stream = path.open('a+')
                        try:
                            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BlockingIOError:
                            busy += 1
                            stream.close()
                        else:
                            streams.append(stream)
                    if busy < maximum and streams:
                        selected = streams.pop(0)
                        selected.seek(0)
                        selected.truncate()
                        json.dump({'account': account, 'port': port, 'pid': os.getpid()}, selected)
                        selected.flush()
                        os.fsync(selected.fileno())
                except BaseException:
                    if selected is not None:
                        selected.close()
                        selected = None
                    raise
                finally:
                    for stream in streams:
                        stream.close()
            if selected is None:
                if not notice:
                    print(f'Menunggu slot Chrome bot (maksimal {maximum} bersamaan).', flush=True)
                    notice = True
                time.sleep(1)
        yield
    finally:
        if selected is not None:
            try:
                selected.seek(0)
                selected.truncate()
                selected.write('{}')
                selected.flush()
                os.fsync(selected.fileno())
            finally:
                selected.close()
