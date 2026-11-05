"""Registri proses terpisah dari DB bot; worker tetap bisa dipantau setelah UI restart."""
from contextlib import closing
from datetime import datetime
from pathlib import Path
import os
import sqlite3
import subprocess
import sys
import threading
import uuid

ACTIVE=('queued','running','stopping')


class Jobs:
    def __init__(self,base,code_root):
        self.base=Path(base);self.code_root=Path(code_root)
        self.db=self.base/'db/control_panel.db'
        self.db.parent.mkdir(parents=True,exist_ok=True)
        with closing(self.connect()) as conn:
            conn.executescript('''CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, account TEXT NOT NULL, kind TEXT NOT NULL,
                status TEXT NOT NULL, created_at TEXT NOT NULL, started_at TEXT,
                finished_at TEXT, heartbeat TEXT, stop_requested INTEGER NOT NULL DEFAULT 0,
                exit_code INTEGER, scheduled_key TEXT, log_path TEXT NOT NULL,
                limit_count INTEGER, content_id INTEGER);
                CREATE UNIQUE INDEX IF NOT EXISTS one_running_account ON runs(account)
                  WHERE status IN ('queued','running','stopping');
                CREATE UNIQUE INDEX IF NOT EXISTS one_schedule_occurrence ON runs(account,scheduled_key)
                  WHERE scheduled_key IS NOT NULL;''')
        self.children=[];self.lock=threading.Lock()

    def connect(self):
        conn=sqlite3.connect(self.db,timeout=30);conn.row_factory=sqlite3.Row
        return conn

    def list(self):
        with closing(self.connect()) as conn:
            return [dict(row) for row in conn.execute('SELECT * FROM runs ORDER BY created_at DESC LIMIT 80')]

    def active(self,account=None):
        with closing(self.connect()) as conn:
            query="SELECT * FROM runs WHERE status IN ('queued','running','stopping')"
            args=()
            if account is not None:query+=' AND account=?';args=(account,)
            return [dict(row) for row in conn.execute(query,args)]

    def launch(self,account,kind='configured',limit_count=None,content_id=None,scheduled_key=None):
        if kind not in ('configured','draft_test'):raise ValueError('Jenis proses tidak valid.')
        run_id=uuid.uuid4().hex
        log_dir=self.base/'logs/panel';log_dir.mkdir(parents=True,exist_ok=True)
        log_path=log_dir/f'{run_id}.log'
        now=datetime.now().isoformat(timespec='seconds')
        try:
            with closing(self.connect()) as conn, conn:
                conn.execute('INSERT INTO runs (id,account,kind,status,created_at,log_path,limit_count,content_id,scheduled_key) VALUES (?,?,?,\'queued\',?,?,?,?,?)',(run_id,account,kind,now,str(log_path),limit_count,content_id,scheduled_key))
        except sqlite3.IntegrityError:
            raise ValueError('Akun masih berjalan atau jadwal ini sudah pernah dijalankan.')
        env={**os.environ,'TIKTOK_BOT_HOME':str(self.base),'PYTHONUNBUFFERED':'1','TIKTOK_BOT_PYTHON':sys.executable}
        try:
            with log_path.open('ab') as log:
                process=subprocess.Popen([sys.executable,'-m','botapp.worker',run_id],cwd=self.code_root,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
            threading.Thread(target=process.wait,daemon=True).start()
            with self.lock:
                self.children=[p for p in self.children if p.poll() is None]+[process]
        except OSError:
            with closing(self.connect()) as conn,conn:
                conn.execute("UPDATE runs SET status='failed',finished_at=? WHERE id=?",(now,run_id))
            raise ValueError('Worker tidak bisa dimulai. Periksa izin proses lokal.')
        return run_id

    def stop(self,run_id):
        with closing(self.connect()) as conn,conn:
            changed=conn.execute("UPDATE runs SET stop_requested=1,status='stopping' WHERE id=? AND status IN ('queued','running','stopping')",(run_id,)).rowcount
            if not changed:raise ValueError('Proses sudah selesai atau tidak ditemukan.')
        return 'Permintaan berhenti dikirim. Klaim video yang hasilnya belum pasti tetap ditahan.'

    def logs(self,run_id):
        with closing(self.connect()) as conn:
            row=conn.execute('SELECT log_path FROM runs WHERE id=?',(run_id,)).fetchone()
        if not row:raise ValueError('Log tidak ditemukan.')
        path=Path(row[0]);root=(self.base/'logs/panel').resolve()
        if path.resolve().parent!=root:raise ValueError('Lokasi log tidak valid.')
        if not path.exists():return ''
        with path.open('rb') as stream:
            stream.seek(max(0,path.stat().st_size-60000))
            return stream.read().decode('utf-8',errors='replace')
