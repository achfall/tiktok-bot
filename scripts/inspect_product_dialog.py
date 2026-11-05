#!/usr/bin/env python3
"""Baca metadata kolom popup upload; tidak klik, ketik, upload, atau publish."""
import json
import requests
import websocket
from daily_orchestrator import send_cdp

EXPRESSION = r'''(() => {
 const visible = el => {const r=el.getBoundingClientRect();return r.width>0&&r.height>0&&getComputedStyle(el).visibility!=='hidden';};
 const dialogs = Array.from(document.querySelectorAll('[role="dialog"],dialog')).filter(visible);
 const fields = Array.from(document.querySelectorAll('input:not([type="password"]):not([type="hidden"]),textarea,[contenteditable="true"]')).filter(visible);
 return {viewport:{width:innerWidth,height:innerHeight},dialogs:dialogs.map(el=>({role:el.getAttribute('role'),text:(el.innerText||'').slice(0,1200)})),fields:fields.map(el=>{const r=el.getBoundingClientRect();return {tag:el.tagName,type:el.type,id:el.id,placeholder:el.getAttribute('placeholder'),ariaLabel:el.getAttribute('aria-label'),labels:Array.from(el.labels||[]).map(x=>x.innerText),maxLength:el.maxLength,rect:{x:r.x,y:r.y,width:r.width,height:r.height},context:(el.parentElement?.innerText||'').slice(0,250)};})};
})()'''


def main():
    response=requests.get('http://127.0.0.1:9221/json',timeout=5)
    response.raise_for_status()
    tabs=[tab for tab in response.json() if tab.get('type')=='page' and 'tiktok.com/tiktokstudio/upload' in tab.get('url','')]
    if not tabs:
        raise SystemExit('Tidak ada tab upload TikTok yang ditemukan.')
    for index, tab in enumerate(tabs, 1):
        print(f"\n=== TAB UPLOAD {index}: {tab.get('title', '')} ===")
        ws = None
        try:
            ws=websocket.create_connection(tab['webSocketDebuggerUrl'],timeout=10,origin='http://127.0.0.1:9221')
            result=send_cdp(ws,'Runtime.evaluate',{'expression':EXPRESSION,'returnByValue':True})
            if 'error' in result or result.get('result',{}).get('exceptionDetails'):
                print('Tidak dapat membaca metadata tab ini.')
                continue
            print(json.dumps(result['result']['result'].get('value'),ensure_ascii=False,indent=2))
        except (websocket.WebSocketException, OSError, KeyError) as error:
            print(f'Tab ini belum bisa diperiksa: {type(error).__name__}')
        finally:
            if ws is not None:
                ws.close()


if __name__=='__main__':
    main()
