#!/usr/bin/env python3
"""Finalisasi SATU video unused di tab upload. Tanpa --submit hanya periksa."""
import argparse
import json
import sqlite3
from urllib.parse import urlparse
import requests
import websocket
from daily_orchestrator import DB_PATH, OVERRIDE_TEXT, CAPTION_READ_JS, eval_js, click, wait_for_submission
from archive_used import archive_after_success
from content_queue import claim_video, begin_submission, finish_success, finish_failure


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('account');parser.add_argument('--submit',action='store_true')
    args=parser.parse_args()
    conn=sqlite3.connect(DB_PATH.as_uri()+'?mode=rw',uri=True,timeout=30)
    try:
        a=conn.execute('SELECT id,cdp_port,publish_mode FROM accounts WHERE slug=?',(args.account,)).fetchone()
        if not a or a[2] not in ('auto','manual_review'):raise SystemExit('Akun/mode tidak valid')
        aid,port,setting=a
        mode='auto' if setting=='auto' else 'manual'
        rows=conn.execute("SELECT c.id,p.id FROM products p JOIN contents c ON ((c.source_folder IS NOT NULL AND c.source_folder=p.folder_path) OR (c.source_folder IS NULL AND c.product_id=p.id)) WHERE p.account_id=? AND p.active=1 AND c.status='unused'",(aid,)).fetchall()
        if len(rows)!=1:raise SystemExit('Helper memerlukan tepat satu video unused pada sumber akun; tidak ada klik.')
        cid,pid=rows[0]
        row=conn.execute('SELECT caption_text FROM captions_history WHERE account_id=? ORDER BY id DESC LIMIT 1',(aid,)).fetchone()
        if not row or not row[0].strip():raise SystemExit('Caption tersimpan kosong')
        caption=row[0]
        def connect(tab):return websocket.create_connection(tab['webSocketDebuggerUrl'],timeout=15,origin=f'http://127.0.0.1:{port}')
        check=f"document.body.innerText.includes({json.dumps(OVERRIDE_TEXT)}) && !Array.from(document.querySelectorAll('[role=dialog],dialog')).some(el=>el.getBoundingClientRect().width>0)"
        response=requests.get(f'http://127.0.0.1:{port}/json',timeout=5);response.raise_for_status()
        candidates=[]
        for tab in response.json():
            if tab.get('type')!='page' or urlparse(tab.get('url','')).path.rstrip('/')!='/tiktokstudio/upload':continue
            ws=connect(tab)
            try:
                if eval_js(ws,check,1) and (eval_js(ws,CAPTION_READ_JS,2) or '').strip()==caption.strip():candidates.append(tab)
            finally:ws.close()
        if len(candidates)!=1:raise SystemExit(f'{len(candidates)} tab cocok; perlu tepat satu.')
        label='Post' if mode=='auto' else 'Save draft'
        button_js="""(() => {const b=Array.from(document.querySelectorAll('button')).filter(el=>(el.innerText||'').trim()===LABEL&&!el.disabled&&el.getBoundingClientRect().width>0);if(b.length!==1)return null;const el=b[0];el.scrollIntoView({block:'center'});const r=el.getBoundingClientRect();return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)};})()""".replace('LABEL',json.dumps(label))
        ws=connect(candidates[0])
        try:
            button=eval_js(ws,button_js,1)
            if not button:raise SystemExit(f'Tombol {label} belum siap; tidak diklik.')
            print(f'Caption dan link cocok; aksi {label}.')
            if not args.submit:
                print('Pemeriksaan saja; belum submit.');return
            claim=claim_video(conn,aid,pid,cid)
            if claim is None:raise SystemExit('Video sudah diklaim akun lain; tidak diklik.')
            try:
                begin_submission(conn,claim,caption)
                mid=click(ws,button['x'],button['y'],2)
                success,url,_=wait_for_submission(ws,mode,mid)
            except BaseException as error:
                finish_failure(conn,claim,caption,str(error));raise
            if success:
                finish_success(conn,claim,mode)
                archive_after_success(conn,cid)
                print(f'Redirect {label} terverifikasi. Cek TikTok Studio.')
            else:
                finish_failure(conn,claim,caption,'Redirect belum terverifikasi')
                print('Hasil belum pasti; video ditahan. Periksa TikTok sebelum retry.')
        finally:ws.close()
    finally:conn.close()


if __name__=='__main__':main()
