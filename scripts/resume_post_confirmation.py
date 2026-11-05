#!/usr/bin/env python3
"""Lanjutkan Post now pada klaim uncertain; tanpa upload ulang."""
import argparse
import sqlite3
import requests
import websocket
from daily_orchestrator import DB_PATH, POST_NOW_BUTTON_JS, CAPTION_READ_JS, eval_js, wait_for_submission
from archive_used import archive_after_success
from content_queue import Claim, resume_submission, finish_success, finish_failure


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('account')
    args=parser.parse_args()
    conn=sqlite3.connect(DB_PATH.as_uri()+'?mode=rw',uri=True,timeout=30)
    try:
        account=conn.execute('SELECT id,cdp_port,publish_mode FROM accounts WHERE slug=?',(args.account,)).fetchone()
        if not account or account[2]!='auto':raise SystemExit('Akun harus mode auto')
        aid,port,_=account
        rows=conn.execute("SELECT c.id,c.claim_product_id,c.file_path,c.claim_token,l.caption_text FROM contents c JOIN posts_log l ON l.id=c.submission_log_id WHERE c.claimed_by=? AND c.status='uncertain'",(aid,)).fetchall()
        if len(rows)!=1:raise SystemExit('Perlu tepat satu klaim uncertain. Periksa manage_queue.py; jangan upload ulang.')
        cid,pid,path,token,caption=rows[0]
        claim=Claim(cid,pid,aid,path,token)
        def connect(tab):return websocket.create_connection(tab['webSocketDebuggerUrl'],timeout=15,origin=f'http://127.0.0.1:{port}')
        response=requests.get(f'http://127.0.0.1:{port}/json',timeout=5);response.raise_for_status()
        candidates=[]
        for tab in response.json():
            if tab.get('type')!='page' or 'tiktok.com/tiktokstudio/upload' not in tab.get('url',''):continue
            ws=connect(tab)
            try:
                if eval_js(ws,POST_NOW_BUTTON_JS,1) and (eval_js(ws,CAPTION_READ_JS,2) or '').strip()==caption.strip():candidates.append(tab)
            finally:ws.close()
        if len(candidates)!=1:raise SystemExit(f'{len(candidates)} tab konfirmasi cocok; tidak ada tombol diklik.')
        ws=connect(candidates[0])
        try:
            resume_submission(conn,claim)
            try:
                success,url,_=wait_for_submission(ws,'auto',1)
            except BaseException as error:
                finish_failure(conn,claim,caption,str(error))
                raise
            if success:
                finish_success(conn,claim,'auto')
                archive_after_success(conn,cid)
                print('BERHASIL: redirect posting terverifikasi. Cek TikTok Studio.')
            else:
                finish_failure(conn,claim,caption,'Redirect belum terverifikasi setelah resume')
                print('Hasil belum pasti. Video tetap ditahan; periksa TikTok, jangan upload ulang.')
        finally:ws.close()
    finally:conn.close()


if __name__=='__main__':main()
