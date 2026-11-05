#!/usr/bin/env python3
"""Isi caption terakhir pada tab dengan link terpasang; tidak upload/publish."""
import json
import sqlite3
import requests
from daily_orchestrator import DB_PATH, CAPTION_READ_JS, eval_js, fill_caption, StepFailed
from bot_settings import effective_settings
from resume_product_link import connect


def main():
    with sqlite3.connect(DB_PATH.as_uri()+'?mode=ro',uri=True) as conn:
        row=conn.execute("SELECT h.caption_text FROM captions_history h JOIN accounts a ON a.id=h.account_id WHERE a.slug='akun1' ORDER BY h.id DESC LIMIT 1").fetchone()
    if not row or not row[0].strip():
        raise SystemExit('Caption tersimpan belum tersedia; halaman tidak diubah.')
    caption=row[0]
    response=requests.get('http://127.0.0.1:9221/json',timeout=5)
    response.raise_for_status()
    candidates=[]
    link_name=effective_settings(DB_PATH.parent.parent,'akun1')['product_link_name']
    link_js=f"document.body.innerText.includes({json.dumps(link_name)}) && !Array.from(document.querySelectorAll('[role=dialog],dialog')).some(el=>el.getBoundingClientRect().width>0)"
    for tab in response.json():
        if tab.get('type')!='page' or 'tiktok.com/tiktokstudio/upload' not in tab.get('url',''):
            continue
        ws=connect(tab)
        try:
            if eval_js(ws,link_js,1) and eval_js(ws,CAPTION_READ_JS,2) is not None:
                candidates.append(tab)
        finally:
            ws.close()
    if len(candidates)!=1:
        raise SystemExit(f'Ditemukan {len(candidates)} tab dengan link terpasang dan editor caption. Tidak ada teks diubah; pastikan hanya satu tab yang sesuai dan popup sudah ditutup.')
    ws=connect(candidates[0])
    try:
        print(f'Memakai caption tersimpan: {caption}')
        fill_caption(ws,caption,1)
        print('Selesai: caption dibaca balik dan cocok. Tidak ada Post/Save draft atau upload ulang.')
    except StepFailed as error:
        raise SystemExit(f'Berhenti: {error}')
    finally:
        ws.close()


if __name__=='__main__':
    main()
