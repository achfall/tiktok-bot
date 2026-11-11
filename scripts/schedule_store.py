"""Slot tayang persisten, terpisah dari schema database bot yang sudah ada."""
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from contextlib import closing
import sqlite3

WIB = ZoneInfo('Asia/Jakarta')
# Shoppable videos dapat mensyaratkan 30 menit; sisakan 5 menit untuk submit.
PLATFORM_MIN_LEAD_MINUTES = 30
MIN_LEAD_MINUTES = PLATFORM_MIN_LEAD_MINUTES + 5


def now_wib():
    return datetime.now(WIB)


def attach(conn, create=False, readonly=False):
    if any(row[1] == 'schedule_meta' for row in conn.execute('PRAGMA database_list')):
        return True
    filename = next(row[2] for row in conn.execute('PRAGMA database_list') if row[1] == 'main')
    path = Path(filename).parent / 'schedule_posts.db' if filename else None
    if not create and (path is None or not path.exists()):
        return False
    if create and conn.in_transaction:
        raise RuntimeError('Hubungkan metadata Schedule sebelum memulai transaksi.')
    target = ':memory:' if path is None else path.as_uri() + '?mode=ro' if readonly else str(path)
    conn.execute('ATTACH DATABASE ? AS schedule_meta', (target,))
    if create:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS schedule_meta.batches (
                id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL,
                quota_day TEXT NOT NULL, first_at TEXT NOT NULL,
                interval_minutes INTEGER NOT NULL, complete INTEGER NOT NULL DEFAULT 0,
                UNIQUE(account_id, quota_day)
            );
            CREATE TABLE IF NOT EXISTS schedule_meta.slots (
                account_id INTEGER NOT NULL, content_id INTEGER NOT NULL,
                batch_id INTEGER NOT NULL, target_at TEXT NOT NULL,
                log_id INTEGER UNIQUE,
                PRIMARY KEY(account_id, content_id), UNIQUE(account_id, target_at)
            );
            CREATE TABLE IF NOT EXISTS schedule_meta.submissions (
                log_id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL,
                content_id INTEGER NOT NULL, target_at TEXT NOT NULL,
                quota_day TEXT NOT NULL
            );
        ''')
    return True


def prepare_store(conn):
    attach(conn, create=True)
    # Dua file hanya crash-atomic dengan rollback journal, bukan WAL/OFF/MEMORY.
    for schema in ('main','schedule_meta'):
        journal=conn.execute(f'PRAGMA {schema}.journal_mode').fetchone()[0]
        if journal not in ('delete','truncate','persist'):
            raise ValueError('Schedule memerlukan rollback journal pada kedua database. Periksa backup dan konfigurasi DB sebelum melanjutkan.')


def reserve_test(conn, claim, target, now=None):
    """Slot E2E tetap; batch_id=0 tidak mengubah batch automasi akun."""
    from content_queue import transaction, require_claim
    now = now or now_wib()
    if target < now + timedelta(minutes=MIN_LEAD_MINUTES):
        raise ValueError('Jam tes Schedule terlalu dekat; pilih waktu baru sebelum mengulang.')
    with transaction(conn):
        require_claim(conn, claim, ('claimed',))
        row = conn.execute('SELECT target_at,log_id FROM schedule_meta.slots WHERE account_id=? AND content_id=?',
                           (claim.account_id,claim.content_id)).fetchone()
        if row:
            if row[1] is not None or row[0] != target.isoformat():
                raise ValueError('Slot tes sudah digunakan atau berubah; periksa antrean.')
            return target
        if conn.execute('SELECT 1 FROM schedule_meta.slots WHERE account_id=? AND target_at=?',
                        (claim.account_id,target.isoformat())).fetchone():
            raise ValueError('Jam tayang sudah dipakai akun ini; pilih waktu lain.')
        conn.execute('INSERT INTO schedule_meta.slots(account_id,content_id,batch_id,target_at) VALUES (?,?,0,?)',
                     (claim.account_id,claim.content_id,target.isoformat()))
        return target


def batch_for(conn, account_id, settings, now=None):
    from content_queue import transaction
    now = now or now_wib()
    prepare_store(conn)
    with transaction(conn):
        row = conn.execute('SELECT id,quota_day,first_at,interval_minutes FROM schedule_meta.batches '
                           'WHERE account_id=? AND complete=0 ORDER BY id DESC LIMIT 1', (account_id,)).fetchone()
        if row:
            return dict(zip(('id', 'quota_day', 'first_at', 'interval_minutes'), row))
        first = now.replace(hour=settings['schedule_start_hour'], minute=settings['schedule_start_minute'], second=0, microsecond=0)
        conn.execute('INSERT INTO schedule_meta.batches '
                     '(account_id,quota_day,first_at,interval_minutes) VALUES (?,?,?,?) '
                     'ON CONFLICT(account_id,quota_day) DO UPDATE SET first_at=excluded.first_at,'
                     'interval_minutes=excluded.interval_minutes,complete=0',
                     (account_id, now.date().isoformat(), first.isoformat(), settings['schedule_interval_minutes']))
        row = conn.execute('SELECT id,quota_day,first_at,interval_minutes FROM schedule_meta.batches '
                           'WHERE account_id=? AND quota_day=?', (account_id, now.date().isoformat())).fetchone()
        conn.execute('UPDATE schedule_meta.batches SET complete=0 WHERE id=?', (row[0],))
        return dict(zip(('id', 'quota_day', 'first_at', 'interval_minutes'), row))


def next_slot(first, interval_minutes, earliest, previous=None):
    """Lewati slot lewat; pertahankan kisi jam awal + n × jeda."""
    step = timedelta(minutes=interval_minutes)
    if interval_minutes < 5 or interval_minutes % 5:
        raise ValueError('Jeda Schedule harus kelipatan 5 menit.')
    threshold = max(earliest, previous + step) if previous else earliest
    if threshold <= first:
        return first
    difference = threshold - first
    count = (difference // step) + (difference % step != timedelta(0))
    return first + count * step


def reserve(conn, claim, batch, now=None):
    from content_queue import transaction, require_claim
    now = now or now_wib()
    earliest = now + timedelta(minutes=MIN_LEAD_MINUTES)
    with transaction(conn):
        require_claim(conn, claim, ('claimed',))
        existing = conn.execute('SELECT target_at,log_id FROM schedule_meta.slots WHERE account_id=? AND content_id=?',
                                (claim.account_id, claim.content_id)).fetchone()
        if existing and existing[1]:
            status = conn.execute('SELECT status FROM posts_log WHERE id=?', (existing[1],)).fetchone()
            if status and status[0] != 'failed':
                raise ValueError('Slot sudah memiliki submit; periksa TikTok sebelum mengulang.')
        last = conn.execute('SELECT MAX(target_at) FROM schedule_meta.slots WHERE account_id=? AND content_id!=?',
                            (claim.account_id, claim.content_id)).fetchone()[0]
        first = datetime.fromisoformat(batch['first_at'])
        candidate = next_slot(first, batch['interval_minutes'], earliest,
                              datetime.fromisoformat(last) if last else None)
        if existing and datetime.fromisoformat(existing[0]) >= earliest:
            candidate = datetime.fromisoformat(existing[0])
        conn.execute('INSERT INTO schedule_meta.slots (account_id,content_id,batch_id,target_at,log_id) VALUES (?,?,?,?,NULL) '
                     'ON CONFLICT(account_id,content_id) DO UPDATE SET batch_id=excluded.batch_id,target_at=excluded.target_at,log_id=NULL',
                     (claim.account_id, claim.content_id, batch['id'], candidate.isoformat()))
        return candidate


def finish_batch(conn, batch):
    from content_queue import transaction
    with transaction(conn):
        conn.execute('UPDATE schedule_meta.batches SET complete=1 WHERE id=?', (batch['id'],))


def history_metadata(conn, log_ids=None):
    # Buka koneksi read-only sendiri: ATTACH URI tidak bekerja pada semua koneksi
    # sqlite3 yang dibuat tanpa uri=True. Batasi pembacaan ke riwayat yang tampil.
    if log_ids is None:
        log_ids=[r[0] for r in conn.execute('SELECT id FROM posts_log ORDER BY id DESC LIMIT 150')]
    if not log_ids:
        return {}
    placeholders=','.join('?' for _ in log_ids)
    main={r[0]:(r[1],r[2]) for r in conn.execute(
        f'SELECT id,account_id,content_id FROM posts_log WHERE id IN ({placeholders})',log_ids)}
    query=f'SELECT log_id,target_at,quota_day,account_id,content_id FROM submissions WHERE log_id IN ({placeholders})'
    if any(r[1]=='schedule_meta' for r in conn.execute('PRAGMA database_list')):
        rows=conn.execute(query.replace('FROM submissions','FROM schedule_meta.submissions'),log_ids).fetchall()
    else:
        filename=next(r[2] for r in conn.execute('PRAGMA database_list') if r[1]=='main')
        path=Path(filename).parent/'schedule_posts.db' if filename else None
        if path is None or not path.is_file():
            return {}
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=15)) as metadata:
            rows=metadata.execute(query,log_ids).fetchall()
    return {r[0]:{'scheduled_at':r[1],'quota_day':r[2]} for r in rows if main.get(r[0])==(r[3],r[4])}
