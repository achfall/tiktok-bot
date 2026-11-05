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


def browser_ready(port):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/version', timeout=2) as response:
            return response.status == 200
    except OSError:
        return False


def prepare_browser(base, account, port):
    if browser_ready(port):
        return
    print('Membuka Chrome khusus akun untuk video berikutnya.', flush=True)
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
            return
        time.sleep(1)
    raise RuntimeError('Chrome CDP belum siap setelah 30 detik; tidak ada upload dilakukan.')


@contextmanager
def browser_slot(base, account, port, maximum):
    """Lock filesystem macOS/Linux. Browser tertahan tetap mengisi slot sampai ditutup.

    Metadata port bertahan jika worker mati/error, sehingga browser yang masih
    terbuka tidak dilupakan saat lock OS otomatis dilepas. Tidak menyentuh antrean.
    """
    root = Path(base)/'db/browser-slots'
    root.mkdir(parents=True, exist_ok=True)
    notice = False
    selected = None
    try:
        while selected is None:
            for index in range(maximum):
                stream = (root/f'{index}.json').open('a+')
                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    stream.close()
                    continue
                try:
                    stream.seek(0)
                    raw = stream.read()
                    previous = json.loads(raw) if raw else {}
                    old_port = previous.get('port')
                    same_account = previous.get('account') == account and old_port == port
                    if old_port and not same_account and browser_ready(old_port):
                        stream.close()
                        continue
                    stream.seek(0)
                    stream.truncate()
                    json.dump({'account': account, 'port': port}, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                    selected = stream
                    break
                except BaseException:
                    stream.close()
                    raise
            if selected is None:
                if not notice:
                    print(f'Menunggu slot Chrome (maksimal {maximum} browser aktif).', flush=True)
                    notice = True
                time.sleep(1)
        yield
    finally:
        if selected is not None:
            # Metadata sengaja dipertahankan: pemegang slot berikutnya mengecek
            # CDP, termasuk browser yang harus diperiksa setelah hasil ambigu.
            selected.close()
