"""Supervisor subprocess; stop kooperatif, heartbeat dan log tanpa mengeksekusi shell."""
from contextlib import closing
from datetime import datetime
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import json

CODE_ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(CODE_ROOT/'scripts'))
from project_paths import PROJECT_ROOT
from botapp.jobs import Jobs


def stop_child(process, timeout=12):
    """Tunggu anak berhenti sebelum supervisor mengakhiri job."""
    if process is None or process.poll() is not None:
        return
    try:
        process.send_signal(signal.SIGINT)
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    except ProcessLookupError:
        process.wait(timeout=5)


def run_command(row, test_options=None):
    command=[sys.executable,'-u',str(CODE_ROOT/'scripts/daily_orchestrator.py'),row['account'],'--publish']
    if row['kind']=='e2e_test':
        from diagnostic_options import validate_options
        options=validate_options(test_options)
        command+=['--retest-content',str(row['content_id']),'--test-options',json.dumps(options)]
    elif row['kind']=='draft_test':
        command+=['--retest-content',str(row['content_id']),'--test-draft']
    elif row['limit_count']:
        command+=['--limit',str(row['limit_count'])]
    return command


def main():
    jobs=Jobs(PROJECT_ROOT,CODE_ROOT);run_id=sys.argv[1]
    now=lambda:datetime.now().isoformat(timespec='seconds')
    with closing(jobs.connect()) as conn,conn:
        row=conn.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
        if not row or row['status'] not in ('queued','stopping'):raise SystemExit('Run tidak tersedia.')
        if row['stop_requested']:
            conn.execute("UPDATE runs SET status='stopped',finished_at=? WHERE id=?",(now(),run_id));return
        conn.execute("UPDATE runs SET status='running',started_at=?,heartbeat=? WHERE id=?",(now(),now(),run_id))
    process=None;stopped=False
    def request_stop(signum,frame):
        nonlocal stopped
        stopped=True
    signal.signal(signal.SIGTERM,request_stop)
    signal.signal(signal.SIGINT,request_stop)
    def pulse():
        nonlocal stopped
        with closing(jobs.connect()) as conn,conn:
            conn.execute('UPDATE runs SET heartbeat=? WHERE id=?',(now(),run_id))
            stop=conn.execute('SELECT stop_requested FROM runs WHERE id=?',(run_id,)).fetchone()[0]
        stopped=stopped or bool(stop)
        return stopped
    try:
        with closing(jobs.connect()) as conn:
            saved=conn.execute('SELECT options_json FROM run_test_options WHERE run_id=?',(run_id,)).fetchone()
        command=run_command(row,json.loads(saved[0]) if saved else None)
        if pulse():
            with closing(jobs.connect()) as conn,conn:
                conn.execute("UPDATE runs SET status='stopped',finished_at=? WHERE id=?",(now(),run_id))
            return
        process=subprocess.Popen(command,cwd=CODE_ROOT,env={**os.environ,'TIKTOK_BOT_HOME':str(PROJECT_ROOT),'PYTHONUNBUFFERED':'1','TIKTOK_SUPERVISOR_PID':str(os.getpid())},stdin=subprocess.DEVNULL)
        deadline=None
        while process.poll() is None:
            with closing(jobs.connect()) as conn,conn:
                conn.execute('UPDATE runs SET heartbeat=? WHERE id=?',(now(),run_id))
                stop=conn.execute('SELECT stop_requested FROM runs WHERE id=?',(run_id,)).fetchone()[0]
            if (stop or stopped) and deadline is None:
                stopped=True;process.send_signal(signal.SIGINT);deadline=time.monotonic()+12
                print('Berhenti diminta; menunggu proses melepas/menahan klaim dengan aman.',flush=True)
            if deadline and time.monotonic()>deadline:
                process.kill();process.wait(timeout=5)
            time.sleep(.75)
        code=process.returncode
        # Alur bot dapat berhenti total dengan exit 0; status queue tetap menjadi acuan hasil.
        status='stopped' if stopped else ('finished' if code==0 else 'failed')
        with closing(jobs.connect()) as conn,conn:
            conn.execute('UPDATE runs SET status=?,exit_code=?,finished_at=?,heartbeat=? WHERE id=?',(status,code,now(),now(),run_id))
    except BaseException:
        try:
            stop_child(process)
        finally:
            with closing(jobs.connect()) as conn,conn:
                conn.execute("UPDATE runs SET status='failed',finished_at=? WHERE id=?",(now(),run_id))
        raise


if __name__=='__main__':main()
