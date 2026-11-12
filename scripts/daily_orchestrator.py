#!/usr/bin/env python3
"""
daily_orchestrator.py
Orchestrator harian: loop semua produk aktif (urutan sort_order) untuk 1 akun,
posting 1 video per produk (video unused pertama di folder produk itu).

publish_mode diambil dari database (accounts.publish_mode), BUKAN cuma dari
flag CLI -- DB yang jadi otoritas final:
  - 'auto'          : bot klik Post sendiri, langsung lanjut ke produk berikutnya
                       dengan jeda random 35-50 menit.
  - 'manual_review' : bot jalan otomatis penuh sama kayak 'auto', TAPI di step
                       akhir klik "Save draft" (bukan Post) -- video gak langsung
                       kepublish, cuma numpuk di TikTok Studio > Drafts. Falla yang
                       nanti publish manual satu-satu dari situ kapan aja mau.
                       Video tetap ditandai used begitu masuk draft (bukan pas
                       benar-benar dipublish manual)         -- video gak akan
                       ke-reuse lagi.
  - flag --publish gak dikasih sama sekali : mode 'none', cuma test 1 produk
    cepat, berhenti pas READY_TO_POST (buat smoke test pipeline doang).

Tiap step ada retry 1x (tunggu lebih lama, coba lagi) -- kalau masih gagal
juga, STOP TOTAL sisa hari itu (bukan skip-lanjut).

Usage:
  python3 daily_orchestrator.py akun1                        # mode 'none': 1 video test
  python3 daily_orchestrator.py akun1 --publish               # mode sesuai DB (auto/manual/schedule)
  python3 daily_orchestrator.py akun1 --publish --limit 5     # batasi max 5 video hari itu
"""

import json
import base64
import random
import sqlite3
import sys
import time
import os
from contextlib import ExitStack, nullcontext
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import requests
import websocket

from project_paths import PROJECT_ROOT
from bot_settings import defaults, validate_values

BASE_DIR = PROJECT_ROOT
DB_PATH = BASE_DIR / "db" / "tiktok_bot.db"
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
from caption_generator import generate_unique_caption  # noqa: E402
from browser_lifecycle import browser_slot, prepare_browser, browser_ready, OWNED_PROCESSES
from browser_process import wait_for_quit
from music_favorites import apply_favorite_music
from wake_recovery import WakeMonitor, WakeInterrupted, run_with_wake_recovery
from upload_overlays import dismiss_known_tours, UPLOAD_OBSTRUCTION_JS
import schedule_store
import schedule_ui
from media_limits import validate_video
from archive_used import archive_after_success  # noqa: E402
from content_queue import (scan_account, claim_video, begin_submission, finish_success,
                           finish_failure, release_preview, prepare_retest, daily_post_count)  # noqa: E402

OVERRIDE_TEXT = defaults()['product_link_name']
UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload"
STEP_DELAY = 1.0
MIN_INTERVAL_MINUTES = 35  # jeda dinamis, biar polanya gak keliatan robotik
MAX_INTERVAL_MINUTES = 50
WINDOW_START_HOUR = 10  # jendela posting: 10.00 - tengah malam (24.00/00.00)
WINDOW_START_MINUTE = 0
DEFAULT_MAX_PER_DAY = 15  # sesuai kesepakatan turun ke 10-15/hari buat versi automasi
WAKE_MONITOR = None


def wait_until(deadline):
    if WAKE_MONITOR is not None:
        WAKE_MONITOR.wait_until(deadline)
        WAKE_MONITOR.ensure_network_after_wake()
    else:
        while time.time() < deadline:
            time.sleep(min(1, max(0, deadline - time.time())))

SCROLL_SNIPPET = "el.scrollIntoView({block:'center',inline:'center'});"


def step(msg):
    print(msg)
    time.sleep(STEP_DELAY)


def send_cdp(ws, method, params=None, msg_id=1):
    if WAKE_MONITOR is not None:
        WAKE_MONITOR.check()
    payload = {"id": msg_id, "method": method, "params": params or {}}
    ws.send(json.dumps(payload))
    deadline = time.monotonic() + 30
    while True:
        if time.monotonic() >= deadline:
            raise StepFailed(f'Chrome tidak menjawab {method} dalam 30 detik.')
        raw = ws.recv()
        if WAKE_MONITOR is not None:
            WAKE_MONITOR.check()
        data = json.loads(raw)
        if data.get("id") == msg_id:
            if 'error' in data:
                raise StepFailed(f'Chrome menolak {method}: {data["error"].get("message","CDP error")}')
            return data


def click(ws, x, y, mid):
    send_cdp(ws, "Input.dispatchMouseEvent",
              {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1}, msg_id=mid)
    send_cdp(ws, "Input.dispatchMouseEvent",
              {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1}, msg_id=mid + 1)
    # Beri halaman waktu bereaksi sebelum membaca atau memilih kontrol berikutnya.
    # Tunggu sesudah klik agar koordinat hasil pemetaan tidak menjadi kedaluwarsa.
    time.sleep(STEP_DELAY)
    return mid + 2


def key_combo(ws, key, code, vkey, ctrl, mid):
    modifiers = (4 if sys.platform == "darwin" else 2) if ctrl else 0
    send_cdp(ws, "Input.dispatchKeyEvent",
              {"type": "keyDown", "modifiers": modifiers, "key": key, "code": code,
               "windowsVirtualKeyCode": vkey}, msg_id=mid)
    send_cdp(ws, "Input.dispatchKeyEvent",
              {"type": "keyUp", "modifiers": modifiers, "key": key, "code": code,
               "windowsVirtualKeyCode": vkey}, msg_id=mid + 1)
    return mid + 2


def eval_js(ws, expr, mid):
    r = send_cdp(ws, "Runtime.evaluate", {"expression": expr, "returnByValue": True}, msg_id=mid)
    if r.get('result',{}).get('exceptionDetails'):
        raise StepFailed('JavaScript halaman gagal dievaluasi; kontrol tidak diklik.')
    return r.get("result", {}).get("result", {}).get("value")


def find_with_retry(ws, expr, mid, label, retry_wait=2.0):
    mid = dismiss_known_tours(ws, mid, eval_js, click, time.sleep)
    pos = eval_js(ws, expr, mid); mid += 1
    if pos:
        return pos, mid
    print(f"    [{label}] gak ketemu di percobaan pertama, tunggu {retry_wait}s terus coba 1x lagi...")
    time.sleep(retry_wait)
    mid = dismiss_known_tours(ws, mid, eval_js, click, time.sleep)
    pos = eval_js(ws, expr, mid); mid += 1
    return pos, mid


def wait_for_window():
    """Tunggu sampai jam dan menit jendela posting lokal."""
    now = datetime.now()
    target = now.replace(hour=WINDOW_START_HOUR, minute=WINDOW_START_MINUTE, second=0, microsecond=0)
    if now < target:
        wait_s = (target - now).total_seconds()
        print(f"Di luar jendela posting (mulai jam {WINDOW_START_HOUR:02d}:{WINDOW_START_MINUTE:02d}) -- "
              f"nunggu {wait_s/60:.0f} menit sampai jendela buka...")
        wait_until(target.timestamp())


class StepFailed(Exception):
    def __init__(self, message):
        self.message = message
        super().__init__(message)


PICKER_DIALOG_JS = r"""
 const visible=e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0&&getComputedStyle(e).visibility!=='hidden'};
 const dialogs=Array.from(document.querySelectorAll('[role="dialog"],dialog')).filter(visible);
 const dialog=dialogs.reverse().find(e=>{const r=e.getBoundingClientRect();const x=Math.max(0,Math.min(innerWidth-1,r.x+r.width/2)),y=Math.max(0,Math.min(innerHeight-1,r.y+r.height/2));return e.contains(document.elementFromPoint(x,y))});
"""
PRODUCT_PICKER_STATE_JS = r"""(() => {""" + PICKER_DIALOG_JS + r"""
 if(!dialog)return {stage:dialogs.length?'unknown':'none'};
 const text=(dialog.innerText||'').trim();
 if(Array.from(dialog.querySelectorAll('input[placeholder="Search products"]')).some(visible)||text.includes('Add product links'))return {stage:'picker'};
 if(text.includes('Link type'))return {stage:'link_type'};
 return {stage:'unknown',text:text.slice(0,200)};
})()"""
ADD_LINK_BUTTON_JS = r"""(() => {
 const root=document.querySelector('[data-e2e="anchor_container"]');if(!root)return null;
 const buttons=Array.from(root.querySelectorAll('button')).filter(e=>(e.innerText||'').trim()==='Add'&&!e.disabled&&e.getAttribute('aria-disabled')!=='true'&&e.getBoundingClientRect().width>0);
 if(buttons.length!==1)return null;const el=buttons[0];el.scrollIntoView({block:'center',inline:'center',behavior:'instant'});
 const r=el.getBoundingClientRect(),x=r.x+r.width/2,y=r.y+r.height/2;
 return el.contains(document.elementFromPoint(x,y))?{x,y}:null;
})()"""
ADD_LINK_NEXT_JS = r"""(() => {""" + PICKER_DIALOG_JS + r"""
 if(!dialog||!(dialog.innerText||'').includes('Link type'))return null;
 if(!Array.from(dialog.querySelectorAll('[role="combobox"]')).some(e=>(e.innerText||'').trim()==='Products'))return null;
 const buttons=Array.from(dialog.querySelectorAll('button')).filter(e=>(e.innerText||'').trim()==='Next'&&visible(e)&&!e.disabled&&e.getAttribute('aria-disabled')!=='true');
 if(buttons.length!==1)return null;const el=buttons[0];el.scrollIntoView({block:'center',behavior:'instant'});
 const r=el.getBoundingClientRect(),x=r.x+r.width/2,y=r.y+r.height/2;
 return el.contains(document.elementFromPoint(x,y))?{x,y}:null;
})()"""


def open_product_picker(ws, mid):
    """Verifikasi transisi Add → Link type → produk, tanpa memilih/submit produk."""
    attempts={'none':0,'link_type':0}
    last_click={'none':-10,'link_type':-10}
    state=None
    for cycle in range(40):
        mid = dismiss_known_tours(ws, mid, eval_js, click, time.sleep)
        state=eval_js(ws,PRODUCT_PICKER_STATE_JS,mid);mid+=1
        stage=state.get('stage') if isinstance(state,dict) else None
        if stage=='picker':return mid
        if stage in attempts and attempts[stage]<2 and cycle-last_click[stage]>=8:
            expression=ADD_LINK_BUTTON_JS if stage=='none' else ADD_LINK_NEXT_JS
            point=eval_js(ws,expression,mid);mid+=1
            if isinstance(point,dict) and 'x' in point and 'y' in point:
                attempts[stage]+=1;last_click[stage]=cycle
                label='Add link' if stage=='none' else 'Next pada dialog Link type'
                print(f'  [{4 if stage=="none" else 5}] Klik {label} (percobaan {attempts[stage]}/2)...')
                mid=click(ws,point['x'],point['y'],mid)
        time.sleep(.5)
    stage=state.get('stage','unknown') if isinstance(state,dict) else 'unknown'
    raise StepFailed(f'Dialog produk belum terbuka setelah menunggu/retry Add link dan Next (state={stage}); submit tidak dilakukan')


def capture_upload_diagnostic(ws, cdp_port):
    """Bukti lokal saat gagal; tidak mengambil cookie, storage, atau input rahasia."""
    try:
        details=eval_js(ws,UPLOAD_OBSTRUCTION_JS,900001)
        if isinstance(details,dict) and (details.get('popups') or details.get('addLinkBlocker')):
            print('  Kondisi popup/penghalang: '+json.dumps(details,ensure_ascii=False)[:2500])
        result=send_cdp(ws,'Page.captureScreenshot',{'format':'png','captureBeyondViewport':False},msg_id=900002)
        data=result.get('result',{}).get('data')
        if not isinstance(data,str):return
        folder=DB_PATH.parent.parent/'logs/diagnostics'
        folder.mkdir(parents=True,exist_ok=True)
        path=folder/f'upload-{cdp_port}-{datetime.now():%Y%m%d-%H%M%S-%f}.png'
        path.write_bytes(base64.b64decode(data,validate=True))
        print(f'  Screenshot kegagalan: {path}')
    except Exception as error:
        print(f'  Bukti popup belum bisa direkam ({type(error).__name__}).')



# Identifikasi berdasarkan label di dialog, tanpa batas koordinat layar.
PRODUCT_NAME_FIELD_JS = r"""(() => {
 const visible = el => {const r=el.getBoundingClientRect();return r.width>0&&r.height>0&&getComputedStyle(el).visibility!=='hidden';};
 const fields=Array.from(document.querySelectorAll('[role="dialog"] input,dialog input'))
   .filter(el => visible(el) && !el.disabled && !el.readOnly && el.type==='text'
     && (Array.from(el.labels||[]).some(label=>(label.innerText||'').trim()==='Product name')
       || el.getAttribute('aria-label')==='Product name'));
 if(fields.length!==1)return null;
 const el=fields[0];
 el.scrollIntoView({block:'center',inline:'center'});
 const r=el.getBoundingClientRect();
 return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2),value:el.value};
})()"""

PRODUCT_NAME_VALUE_JS = r"""(() => {
 const fields=Array.from(document.querySelectorAll('[role="dialog"] input,dialog input'))
   .filter(el=>el.getBoundingClientRect().width>0 && el.type==='text'
     && (Array.from(el.labels||[]).some(label=>(label.innerText||'').trim()==='Product name')
       || el.getAttribute('aria-label')==='Product name'));
 return fields.length===1 ? fields[0].value : null;
})()"""

PRODUCT_NAME_SELECT_JS = PRODUCT_NAME_VALUE_JS.replace(
    "return fields.length===1 ? fields[0].value : null;",
    """if(fields.length!==1)return false;
 const el=fields[0];el.focus();el.select();
 return document.activeElement===el && el.selectionStart===0 && el.selectionEnd===el.value.length;""",
)

PRODUCT_ADD_BUTTON_JS = r"""(() => {
 const fields=Array.from(document.querySelectorAll('[role="dialog"] input,dialog input'))
   .filter(el=>el.getBoundingClientRect().width>0 && el.type==='text'
     && (Array.from(el.labels||[]).some(label=>(label.innerText||'').trim()==='Product name')
       || el.getAttribute('aria-label')==='Product name'));
 if(fields.length!==1)return null;
 const dialog=fields[0].closest('[role="dialog"],dialog');
 const buttons=Array.from(dialog.querySelectorAll('button'))
   .filter(el=>(el.innerText||'').trim()==='Add' && el.getBoundingClientRect().width>0);
 if(buttons.length!==1)return null;
 const el=buttons[0];el.scrollIntoView({block:'center',inline:'center'});
 const r=el.getBoundingClientRect();
 return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2),disabled:el.disabled||el.getAttribute('aria-disabled')==='true'};
})()"""


def attach_product_link(ws, mid, product_link_name=None):
    """Ganti nama dan klik Add di dialog produk saja; tidak Post/Save draft."""
    link_name = validate_values({'product_link_name': product_link_name if product_link_name is not None else OVERRIDE_TEXT}, partial=True)['product_link_name']
    step(f'  [9] Ganti nama produk jadi: "{link_name}"...')
    field, mid = find_with_retry(ws, PRODUCT_NAME_FIELD_JS, mid, "product name field")
    if not field:
        raise StepFailed("Product name field not found or ambiguous")
    mid = click(ws, field["x"], field["y"], mid)
    time.sleep(0.5)
    # Pilih teks lewat DOM agar tidak tergantung shortcut native macOS/Linux.
    selected = eval_js(ws, PRODUCT_NAME_SELECT_JS, mid)
    mid += 1
    if not selected:
        raise StepFailed("Product name selection failed; text and Add unchanged")
    result = send_cdp(ws, "Input.insertText", {"text": link_name}, msg_id=mid)
    mid += 1
    if "error" in result:
        raise StepFailed("Product name text insertion failed; Add not clicked")
    time.sleep(0.6)
    value = eval_js(ws, PRODUCT_NAME_VALUE_JS, mid)
    mid += 1
    if value != link_name:
        raise StepFailed(f"Product name verification failed (actual={value!r}); Add not clicked")
    step("  [10] Klik Add (finalisasi link)...")
    button, mid = find_with_retry(ws, PRODUCT_ADD_BUTTON_JS, mid, "Add in product dialog")
    if not button or button["disabled"]:
        raise StepFailed("Product dialog Add button missing or disabled")
    mid = click(ws, button["x"], button["y"], mid)
    time.sleep(1.5)
    # Dialog harus tertutup setelah Add; jangan laporkan siap bila masih terbuka.
    remaining = eval_js(ws, PRODUCT_NAME_VALUE_JS, mid)
    mid += 1
    if remaining is not None:
        raise StepFailed("Product dialog still open after Add; inspect browser")
    return mid



CAPTION_SELECT_JS = r"""(() => {
 const fields=Array.from(document.querySelectorAll('.public-DraftEditor-content'))
   .filter(el=>el.isContentEditable && el.getBoundingClientRect().width>0);
 if(fields.length!==1)return false;
 const el=fields[0];el.scrollIntoView({block:'center',inline:'center'});el.focus();
 const range=document.createRange();range.selectNodeContents(el);
 const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);
 return document.activeElement===el && selection.rangeCount===1 && selection.getRangeAt(0).commonAncestorContainer===el;
})()"""

CAPTION_READ_JS = r"""(() => {
 const fields=Array.from(document.querySelectorAll('.public-DraftEditor-content'))
   .filter(el=>el.isContentEditable && el.getBoundingClientRect().width>0);
 return fields.length===1 ? fields[0].innerText : null;
})()"""


def fill_caption(ws, caption_text, mid):
    """Ganti isi DraftEditor lewat input CDP, lalu baca balik sebelum lanjut."""
    if not caption_text or not caption_text.strip():
        raise StepFailed("Caption kosong; editor tidak diubah")
    selected = eval_js(ws, CAPTION_SELECT_JS, mid)
    mid += 1
    if not selected:
        raise StepFailed("Caption editor selection failed; no text inserted")
    response = send_cdp(ws, "Input.insertText", {"text": caption_text}, msg_id=mid)
    mid += 1
    if "error" in response:
        raise StepFailed("Caption insertion failed")
    # Beri waktu editor React memproses input dan render ulang.
    time.sleep(1)
    actual = eval_js(ws, CAPTION_READ_JS, mid)
    mid += 1
    normalize = lambda text: text.replace('\u00a0', ' ').replace('\r\n', '\n').strip()
    if not isinstance(actual, str) or normalize(actual) != normalize(caption_text):
        raise StepFailed(f"Caption verification failed (actual={actual!r}); pipeline stopped")
    print("  Caption pada editor sudah diverifikasi.")
    return mid



POST_NOW_BUTTON_JS = r"""(() => {
 const visible=el=>{const r=el.getBoundingClientRect();return r.width>0&&r.height>0&&getComputedStyle(el).visibility!=='hidden';};
 const buttons=Array.from(document.querySelectorAll('button')).filter(el=>visible(el)&&!el.disabled&&el.getAttribute('aria-disabled')!=='true'&&(el.innerText||'').trim().toLowerCase()==='post now');
 const matches=buttons.filter(button=>{
   for(let el=button.parentElement;el&&el!==document.body;el=el.parentElement){
     const text=(el.innerText||'').replace(/\s+/g,' ').toLowerCase();
     if(text.length>2000)break;
     if(text.includes('continue to post?')&&text.includes('still checking your video'))return true;
   }
   return false;
 });
 if(matches.length!==1)return null;
 const el=matches[0];el.scrollIntoView({block:'center'});const r=el.getBoundingClientRect();
 return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)};
})()"""


def confirm_pending_post(ws, mid):
    """Klik sekali hanya konfirmasi pemeriksaan video yang telah dikenali."""
    button = eval_js(ws, POST_NOW_BUTTON_JS, mid)
    mid += 1
    if not button:
        return False, mid
    print('  Konfirmasi "Continue to post?" terdeteksi; klik Post now.')
    mid = click(ws, button["x"], button["y"], mid)
    return True, mid


COPYRIGHT_STATE_JS = r"""(()=>{
 const container=document.querySelector('[data-e2e="copyright_container"]');
 const root=container?.closest('.copyright-check');if(!root)return 'unknown';
 const visible=e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0&&getComputedStyle(e).visibility!=='hidden';};
 const status=Array.from(root.querySelectorAll('.status-result')).find(visible);
 if(status?.classList.contains('status-success')&&/no issues found/i.test(status.innerText))return 'ready';
 if(status?.classList.contains('status-checking'))return 'checking';
 if(status?.classList.contains('status-error')||status?.classList.contains('status-warn'))return 'failed';
 return 'unknown';
})()"""

COPYRIGHT_CANCEL_JS = r"""(()=>{
 const visible=e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0&&getComputedStyle(e).visibility!=='hidden';};
 const matches=[];
 for(const b of document.querySelectorAll('button')){
  if(!visible(b)||b.disabled||b.innerText.trim().toLowerCase()!=='post now')continue;
  for(let p=b.parentElement;p&&p!==document.body;p=p.parentElement){
   const text=(p.innerText||'').replace(/\s+/g,' ').toLowerCase();if(text.length>2000)break;
   if(text.includes('continue to post?')&&text.includes('still checking your video')){
    const cancel=Array.from(p.querySelectorAll('button')).filter(e=>visible(e)&&!e.disabled&&e.innerText.trim().toLowerCase()==='cancel');
    if(cancel.length===1)matches.push(cancel[0]);break;
   }
  }
 }
 if(matches.length!==1)return null;const el=matches[0];const r=el.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};
})()"""


def resolve_copyright_prompt(ws, mode, mid, schedule_context=None, check_attempts=91):
    """Tangani popup sekali, dengan aksi akhir tetap mengikuti mode yang dipilih."""
    if mode == 'auto':
        return confirm_pending_post(ws, mid)
    button = eval_js(ws, POST_NOW_BUTTON_JS, mid)
    mid += 1
    if not button:
        return False, mid
    if mode == 'schedule':
        target = schedule_context.get('target') if schedule_context else None
        if not target or target < schedule_store.now_wib()+timedelta(minutes=schedule_store.PLATFORM_MIN_LEAD_MINUTES):
            raise StepFailed('Dialog copyright muncul tetapi waktu Schedule terlalu dekat; hasil ditahan untuk diperiksa.')
        intact = eval_js(ws, schedule_ui.confirmation_guard(target), mid)
        mid += 1
        if intact is not True:
            raise StepFailed('Pilihan tanggal/jam Schedule berubah pada dialog copyright; konfirmasi dibatalkan.')
        print('  Dialog copyright: tanggal/jam Schedule terverifikasi; lanjutkan konfirmasi.')
        return True, click(ws, button['x'], button['y'], mid)
    if mode != 'manual':
        raise StepFailed('Dialog copyright pada mode yang tidak dikenal; tidak ada konfirmasi.')
    # Label Post now tidak dijadikan persetujuan publish untuk permintaan draft.
    cancel = eval_js(ws, COPYRIGHT_CANCEL_JS, mid)
    mid += 1
    if not cancel:
        raise StepFailed('Tombol Cancel dialog copyright tidak dapat diverifikasi; draft ditahan.')
    mid = click(ws, cancel['x'], cancel['y'], mid)
    print('  Dialog copyright pada Simpan draft: tunggu pemeriksaan, lalu lanjut Save draft.')
    for attempt in range(check_attempts):
        state = eval_js(ws, COPYRIGHT_STATE_JS, mid)
        mid += 1
        if state == 'ready':
            break
        if state != 'checking':
            raise StepFailed('Pemeriksaan copyright belum berhasil; Save draft tidak diulang.')
        if attempt < check_attempts-1:
            time.sleep(10)
    else:
        raise StepFailed('Pemeriksaan copyright belum selesai setelah 15 menit; draft ditahan.')
    draft, mid = find_with_retry(ws, """(()=>{const buttons=Array.from(document.querySelectorAll('button'))
      .filter(b=>b.innerText.trim()==='Save draft'&&!b.disabled&&b.getAttribute('aria-disabled')!=='true'&&b.getBoundingClientRect().width>0);
      if(buttons.length!==1)return null;const b=buttons[0];b.scrollIntoView({block:'center'});const r=b.getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2};})()""", mid, 'Save draft setelah copyright')
    if not draft:
        raise StepFailed('Save draft tidak ditemukan setelah pemeriksaan copyright.')
    return True, click(ws, draft['x'], draft['y'], mid)


def wait_for_submission(ws, mode, mid, attempts=45, schedule_context=None):
    confirmed = False
    url = None
    for _ in range(attempts):
        time.sleep(1)
        url = eval_js(ws, "location.href", mid)
        mid += 1
        parsed = urlparse(url or "")
        content = parsed.hostname in ("www.tiktok.com", "tiktok.com") and parsed.path.rstrip("/") == "/tiktokstudio/content"
        draft = parse_qs(parsed.query).get("tab") == ["draft"]
        if content and mode == 'schedule':
            if not draft and schedule_context:
                verified = eval_js(ws, schedule_ui.result_expression(schedule_context['caption'], schedule_context['target']), mid)
                mid += 1
                if verified is True:
                    return True, url, mid
        elif content and (not draft if mode == "auto" else draft):
            return True, url, mid
        if not confirmed and parsed.hostname in ('www.tiktok.com','tiktok.com') and parsed.path.rstrip('/') == '/tiktokstudio/upload':
            confirmed, mid = resolve_copyright_prompt(ws, mode, mid, schedule_context)
    return False, url, mid


def wait_for_upload_input(ws, mid):
    """Tunggu mount input upload; baca ulang DOM tanpa reload/klik/upload."""
    for attempt in range(11):
        doc = send_cdp(ws, "DOM.getDocument", msg_id=mid); mid += 1
        root_id = doc.get("result", {}).get("root", {}).get("nodeId")
        if root_id:
            result = send_cdp(ws, "DOM.querySelector", {"nodeId": root_id, "selector": "input[type='file']"}, msg_id=mid); mid += 1
            node_id = result.get("result", {}).get("nodeId")
            if node_id:
                return node_id, mid
        if attempt == 0:
            print("    Input video belum siap; tunggu hingga 20 detik...", flush=True)
        if attempt < 10:
            time.sleep(2)
    raise StepFailed("File input not found setelah menunggu 20 detik; periksa halaman upload TikTok.")


def verify_browser_profile(ws, expected_profile, mid):
    """Periksa profil tab baru sebelum navigasi TikTok atau upload apa pun."""
    actual = None
    for _ in range(20):
        actual = eval_js(ws, "document.querySelector('#profile_path')?.textContent", mid)
        mid += 1
        if actual:
            break
        time.sleep(.1)
    if not isinstance(actual, str) or Path(actual).resolve() != Path(expected_profile).resolve():
        raise StepFailed('Profil internal Chrome tidak sesuai kunci akun. Buka Chrome dari panel akun untuk memilih profil yang benar, lalu coba lagi. Tidak ada upload dilakukan.')
    print(f'  Profil internal Chrome terverifikasi: {Path(actual).name}')
    return mid


def post_one_video(cdp_port, video_path, caption_text, tiktok_product_id, mode, before_submit=None, product_link_name=None, music_context=None, expected_profile=None, schedule_context=None):
    """Jalanin 1 video full pipeline. mode: 'none' | 'auto' | 'manual' | 'schedule'.
    Return (success: bool, error_message: str|None)."""
    if expected_profile and not Path(expected_profile).is_dir():
        return False, 'Profil internal Chrome yang dikunci tidak ditemukan. Pulihkan profil atau login ulang dan verifikasi identitas sebelum mengubah kunci. Tidak ada upload dilakukan.'
    initial_url = "chrome://version/" if expected_profile else UPLOAD_URL
    ws = None
    tab_id = None
    mid = 1
    try:
        resp = requests.put(f"http://127.0.0.1:{cdp_port}/json/new?{initial_url}", timeout=10)
        resp.raise_for_status()
        tab = resp.json()
        tab_id = tab["id"]
        endpoint=tab['webSocketDebuggerUrl']
        parsed_endpoint=urlparse(endpoint)
        if parsed_endpoint.scheme!='ws' or parsed_endpoint.hostname not in ('127.0.0.1','localhost') or parsed_endpoint.port!=cdp_port:
            raise StepFailed('Endpoint tab Chrome tidak sesuai port akun; tidak ada upload dilakukan.')
        ws = websocket.create_connection(endpoint, timeout=15)

        if expected_profile:
            mid = verify_browser_profile(ws, expected_profile, mid)
            send_cdp(ws, "Page.navigate", {"url": UPLOAD_URL}, msg_id=mid); mid += 1
        send_cdp(ws, "Page.enable", msg_id=mid); mid += 1
        send_cdp(ws, "DOM.enable", msg_id=mid); mid += 1

        step("  [1] Tunggu halaman upload load...")
        input_node_id, mid = wait_for_upload_input(ws, mid)

        step("  [2] Upload video...")
        send_cdp(ws, "DOM.setFileInputFiles", {"files": [video_path], "nodeId": input_node_id}, msg_id=mid); mid += 1
        time.sleep(6)
        mid = dismiss_known_tours(ws, mid, eval_js, click, time.sleep)

        step("  [3] Isi caption...")
        mid = fill_caption(ws, caption_text, mid)

        mid = open_product_picker(ws, mid)
        time.sleep(4)  # list produk showcase butuh waktu render penuh

        step("  [5b] Cek toggle My Shop / Showcase products (opsional, beda per akun)...")
        # Cuma klik "Showcase products" kalau BENERAN ada toggle (tombol "My Shop" juga
        # ketemu) -- kalau cuma ada 1 opsi doang (kayak akun1), itu bukan toggle, jangan diklik.
        has_toggle = eval_js(ws, "(()=>{return Array.from(document.querySelectorAll('button')).some(b=>(b.innerText||'').trim().toLowerCase()==='my shop');})()", mid); mid += 1
        if has_toggle:
            toggle_pos = eval_js(ws, f"(()=>{{const b=Array.from(document.querySelectorAll('button')).filter(b=>(b.innerText||'').trim().toLowerCase()==='showcase products');if(!b.length)return null;const el=b[0];{SCROLL_SNIPPET}const r=el.getBoundingClientRect();return{{x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)}};}})()", mid); mid += 1
            if toggle_pos:
                print("    Toggle My Shop/Showcase products ketemu, klik 'Showcase products'...")
                # Klik + verifikasi field search beneran muncul, retry klik 1x kalau belum
                for attempt in range(2):
                    mid = click(ws, toggle_pos["x"], toggle_pos["y"], mid)
                    time.sleep(1.5)
                    switched = eval_js(ws, "(()=>!!document.querySelector('input[placeholder=\"Search products\"]'))()", mid); mid += 1
                    if switched:
                        break
                    if attempt == 0:
                        print("    Tab belum kepindah, tunggu lagi & klik ulang 1x...")
                        time.sleep(1.5)
                if not switched:
                    print("    WARNING: tab gak kunjung pindah ke Showcase products setelah 2x coba.")
        else:
            print("    Gak ada toggle beneran (cuma 1 opsi Showcase products doang), skip klik.")

        step(f"  [6] Search produk by ID: {tiktok_product_id}...")
        pos, mid = find_with_retry(ws, f"(()=>{{const el=document.querySelector('input[placeholder=\"Search products\"]');if(!el)return null;{SCROLL_SNIPPET}const r=el.getBoundingClientRect();return{{x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)}};}})()", mid, "search field")
        if not pos:
            raise StepFailed("Search field not found")
        mid = click(ws, pos["x"], pos["y"], mid)
        time.sleep(0.4)
        send_cdp(ws, "Input.insertText", {"text": tiktok_product_id}, msg_id=mid); mid += 1
        time.sleep(0.4)
        mid = key_combo(ws, "Enter", "Enter", 13, False, mid)
        time.sleep(2)

        step("  [7] Klik radio produk...")
        radio_expr = ("(()=>{const radios=Array.from(document.querySelectorAll('input[type=\"radio\"]'))"
                       ".filter(e=>e.getBoundingClientRect().width>0);"
                       "const candidates=radios.filter(e=>{let node=e,text='';"
                       "for(let l=0;l<4;l++){if(!node.parentElement)break;node=node.parentElement;"
                       "const t=(node.innerText||'').trim();if(t.length>text.length)text=t;}"
                       "return !text.includes('When to post');});"
                       "if(!candidates.length)return null;const el=candidates[0];"
                       f"{SCROLL_SNIPPET}"
                       "const r=el.getBoundingClientRect();"
                       "return{x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)};})()")
        pos, mid = find_with_retry(ws, radio_expr, mid, "product radio")
        if not pos:
            raise StepFailed(f"Product search returned no results for ID {tiktok_product_id}")
        mid = click(ws, pos["x"], pos["y"], mid)
        time.sleep(1)

        step("  [8] Klik Next (setelah pilih produk)...")
        next_info, mid = find_with_retry(ws, f"(()=>{{const b=Array.from(document.querySelectorAll('button')).filter(b=>(b.innerText||'').trim()==='Next');if(!b.length)return null;const el=b[b.length-1];{SCROLL_SNIPPET}const r=el.getBoundingClientRect();return{{x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2),disabled:el.disabled}};}})()", mid, "Next btn (popup 2)")
        if not next_info or next_info["disabled"]:
            raise StepFailed("Next disabled after product select")
        mid = click(ws, next_info["x"], next_info["y"], mid)
        time.sleep(2)

        mid = attach_product_link(ws, mid, product_link_name)

        if music_context is not None:
            mid = apply_favorite_music(ws, mid, music_context['base'], music_context['account'],
                                       music_context['content_id'], eval_js, click)
            if eval_js(ws, CAPTION_READ_JS, mid) != caption_text:
                raise StepFailed('Caption berubah setelah Save editor musik; submit tidak dilakukan')
            mid += 1
            link_name = product_link_name if product_link_name is not None else OVERRIDE_TEXT
            link_check = f"(()=>{{const e=document.querySelector('[data-e2e=\"anchor_container\"]');return !!e&&(e.innerText||'').includes({json.dumps(link_name)});}})()"
            if eval_js(ws, link_check, mid) is not True:
                raise StepFailed('Link produk tidak terverifikasi setelah Save editor musik; submit tidak dilakukan')
            mid += 1

        print("  >>> READY_TO_POST -- link produk udah ke-attach.")

        if mode == "none":
            print("  >>> Mode test (gak ada --publish), berhenti di sini.")
            try:
                requests.get(f"http://127.0.0.1:{cdp_port}/json/close/{tab_id}", timeout=3)
            except Exception:
                pass
            return True, None

        if mode == 'schedule':
            if not schedule_context:
                raise StepFailed('Metadata batch Schedule belum tersedia.')
            target = schedule_context['reserve']()
            schedule_context.update(target=target, caption=caption_text)
            print('  Jadwal tayang WIB:', target.isoformat(), flush=True)
            mid = schedule_ui.configure(ws, mid, target, eval_js, click)
            action_btn, mid = find_with_retry(ws, """(()=>{const b=document.querySelector('button[data-e2e="post_video_button"]');
                if(!b||b.disabled||b.getAttribute('aria-disabled')==='true'||b.innerText.trim()!=='Schedule')return null;
                b.scrollIntoView({block:'center'});const r=b.getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2};})()""", mid, 'Schedule btn')
            if not action_btn:
                raise StepFailed('Tombol Schedule belum siap; submit dibatalkan.')
            action_label = 'TERJADWAL'
        elif mode == "auto":
            step("  [11] Cari & klik tombol Post...")
            action_btn, mid = find_with_retry(ws, f"(()=>{{const b=Array.from(document.querySelectorAll('button')).filter(b=>(b.innerText||'').trim()==='Post'&&!b.disabled);if(!b.length)return null;const el=b[b.length-1];{SCROLL_SNIPPET}const r=el.getBoundingClientRect();return{{x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)}};}})()", mid, "Post btn")
            if not action_btn:
                raise StepFailed("Post button not found")
            success_marker = "tiktokstudio/content"
            action_label = "PUBLISH"
        else:  # mode == "manual" -> bot klik "Save draft" sendiri, bukan Post
            step("  [11] Cari & klik tombol Save draft...")
            action_btn, mid = find_with_retry(ws, f"(()=>{{const b=Array.from(document.querySelectorAll('button')).filter(b=>(b.innerText||'').trim()==='Save draft'&&!b.disabled);if(!b.length)return null;const el=b[b.length-1];{SCROLL_SNIPPET}const r=el.getBoundingClientRect();return{{x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)}};}})()", mid, "Save draft btn")
            if not action_btn:
                raise StepFailed("Save draft button not found")
            success_marker = "tab=draft"
            action_label = "SIMPAN KE DRAFT"

        # Simpan state persisten SEBELUM klik; hasil ambigu tidak boleh dipakai akun lain.
        if mode == 'schedule' and schedule_context['target'] < schedule_store.now_wib() + timedelta(minutes=schedule_store.PLATFORM_MIN_LEAD_MINUTES):
            raise StepFailed('Slot Schedule terlalu dekat sebelum klik; submit dibatalkan.')
        if before_submit is not None:
            before_submit()
        mid = click(ws, action_btn["x"], action_btn["y"], mid)

        step("  [12] Verifikasi redirect...")
        if mode == 'schedule':
            success, url, mid = wait_for_submission(ws, mode, mid, schedule_context=schedule_context)
        else:
            success, url, mid = wait_for_submission(ws, mode, mid)

        if success:
            print(f"  BERHASIL {action_label}! URL: {url}")
            try:
                requests.get(f"http://127.0.0.1:{cdp_port}/json/close/{tab_id}", timeout=3)
            except Exception:
                pass
            return True, None
        else:
            return False, f"Submission not verified after timeout (last url: {url}); check TikTok before retrying"

    except WakeInterrupted as e:
        return False, str(e)
    except (StepFailed, ValueError) as e:
        if expected_profile and 'Profil internal Chrome tidak sesuai' in str(e):
            try:
                requests.get(f'http://127.0.0.1:{cdp_port}/json/close/{tab_id}', timeout=3)
            except Exception:
                pass
        else:
            if ws is not None:
                capture_upload_diagnostic(ws, cdp_port)
        return False, str(e)
    except Exception as e:
        if ws is not None:
            capture_upload_diagnostic(ws, cdp_port)
        return False, f"Unexpected error: {e}"
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        elif tab_id is not None:
            try:
                requests.get(f'http://127.0.0.1:{cdp_port}/json/close/{tab_id}', timeout=3)
            except Exception:
                pass


def get_account(cur, account_slug):
    cur.execute("SELECT id, cdp_port, publish_mode, caption_style, chrome_profile_path, active FROM accounts WHERE slug=?", (account_slug,))
    row = cur.fetchone()
    if not row:
        raise RuntimeError(f"GAK KETEMU akun '{account_slug}'.")
    return {"id": row[0], "cdp_port": row[1], "publish_mode": row[2], "caption_style": row[3], "chrome_profile_path": row[4], 'active':row[5]}


def get_active_products(cur, account_id):
    """Produk aktif yang BELUM sukses publish hari ini (cek dari posts_log)."""
    cur.execute(
        """SELECT id, folder_name, tiktok_product_id FROM products
           WHERE account_id=? AND active=1
           AND id NOT IN (
               SELECT product_id FROM posts_log
               WHERE product_id IS NOT NULL AND account_id=? AND (status IN ('submitting','uncertain') OR (status IN ('success','draft','scheduled') AND DATE(published_at,'localtime')=DATE('now','localtime')))
           )
           ORDER BY sort_order ASC""",
        (account_id, account_id),
    )
    return cur.fetchall()


def close_account_browser(cdp_port):
    """Quit instance akun; fallback SIGTERM hanya pada PID milik bot yang terverifikasi."""
    ws = None
    try:
        response = requests.get(f'http://127.0.0.1:{cdp_port}/json/version', timeout=5)
        response.raise_for_status()
        endpoint = response.json()['webSocketDebuggerUrl']
        parsed = urlparse(endpoint)
        if parsed.scheme != 'ws' or parsed.hostname not in ('127.0.0.1', 'localhost') or parsed.port != cdp_port:
            raise ValueError('Endpoint browser tidak sesuai port akun')
        ws = websocket.create_connection(endpoint, timeout=5)
        identity=OWNED_PROCESSES.get(cdp_port)
        if identity is not None and identity['endpoint']!=endpoint:
            print('Identitas instance Chrome berubah; Quit dibatalkan.')
            return False
        ws.send(json.dumps({'id': 1, 'method': 'Browser.close'}))
        if identity is not None:
            if wait_for_quit(cdp_port,identity,browser_ready):
                OWNED_PROCESSES.pop(cdp_port,None)
                print('Chrome khusus akun sudah Quit; proses aplikasi berhenti.')
                return True
            print('Chrome belum terverifikasi Quit; periksa browser sebelum melanjutkan.')
            return False
        for _ in range(40):
            if not browser_ready(cdp_port):
                print('Koneksi Chrome akun ditutup melalui Browser.close.')
                return True
            time.sleep(.25)
        print('Peringatan: Chrome masih menutup; slot belum tersedia untuk akun lain.')
        return False
    except Exception as error:
        print(f'Peringatan: Chrome belum bisa ditutup otomatis ({type(error).__name__}). Periksa Chrome sebelum melanjutkan.')
        return False
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass  # Chrome bisa memutus socket sesudah Browser.close.


def run_day():
    if len(sys.argv) < 2:
        print("Usage: python3 daily_orchestrator.py <account_slug> [--publish] [--limit N]")
        sys.exit(1)

    account_slug = sys.argv[1]
    from bot_settings import effective_settings
    runtime = effective_settings(DB_PATH.parent.parent, account_slug)
    from diagnostic_options import cli_options
    test_options = cli_options(sys.argv)
    if test_options and test_options['music'] != 'account':
        runtime['music_enabled'] = test_options['music'] == 'on'
    test_schedule = bool(test_options and test_options['mode'] == 'schedule')
    global STEP_DELAY, MIN_INTERVAL_MINUTES, MAX_INTERVAL_MINUTES, WINDOW_START_HOUR, WINDOW_START_MINUTE, DEFAULT_MAX_PER_DAY
    STEP_DELAY = runtime['step_delay']
    MIN_INTERVAL_MINUTES = runtime['min_interval_minutes']
    MAX_INTERVAL_MINUTES = runtime['max_interval_minutes']
    WINDOW_START_HOUR = runtime['window_start_hour']
    WINDOW_START_MINUTE = runtime['window_start_minute']
    DEFAULT_MAX_PER_DAY = runtime['max_per_day']
    cli_publish = "--publish" in sys.argv
    max_per_day = DEFAULT_MAX_PER_DAY
    if "--limit" in sys.argv:
        max_per_day = min(max_per_day, max(0, int(sys.argv[sys.argv.index("--limit") + 1])))

    conn = sqlite3.connect(DB_PATH, timeout=30)
    cur = conn.cursor()

    account = get_account(cur, account_slug)
    if not account['active']:
        conn.close()
        raise SystemExit('Akun tidak aktif; tidak ada upload dilakukan.')
    from account_sources import source as account_source
    flat = account_source(conn, account["id"])

    force_test_draft = "--test-draft" in sys.argv
    if force_test_draft and (not cli_publish or "--retest-content" not in sys.argv):
        conn.close()
        raise SystemExit("--test-draft hanya untuk --publish --retest-content <id>.")
    if test_options:
        mode = {'manual_review':'manual','auto':'auto','schedule':'schedule'}[test_options['mode']]
        print(f"TES E2E: {test_options['mode']}; musik {'aktif' if runtime['music_enabled'] else 'nonaktif'}; pengaturan akun tidak diubah.")
    elif force_test_draft:
        mode = "manual"
        print("TES DRAFT: aksi akhir Save draft; konfigurasi publish akun tidak diubah.")
    elif not cli_publish:
        mode = "none"
    elif account["publish_mode"] == "auto":
        mode = "auto"
    elif account['publish_mode'] == 'schedule':
        mode = 'schedule'
    else:
        if account['publish_mode'] != 'manual_review':
            conn.close()
            raise SystemExit('Mode akun tidak dikenal; tidak ada upload dilakukan.')
        mode = "manual"
        print(f"CATATAN: publish_mode akun '{account_slug}' di database = '{account['publish_mode']}'"
              f" (bukan 'auto') -- jalan otomatis penuh tapi simpan ke DRAFT (bukan publish langsung)."
              f" Review & publish manual dari TikTok Studio > Drafts kapan aja.")
        print(f"(Kalau mau full-auto publish langsung, ubah dulu: sqlite3 db/tiktok_bot.db "
              f"\"UPDATE accounts SET publish_mode='auto' WHERE slug='{account_slug}';\")\n")

    retest_content = None
    retest_id_to_claim = None
    new_count = scan_account(conn, account["id"])
    print(f"Scan awal: {new_count} video baru; arsip tidak dipindai.")
    batch = schedule_store.batch_for(conn, account['id'], runtime) if mode == 'schedule' and not test_schedule else None
    if test_schedule:
        schedule_store.prepare_store(conn)
    quota_day = batch['quota_day'] if batch else schedule_store.now_wib().date().isoformat() if test_schedule else None
    remaining = max(0, max_per_day - daily_post_count(conn, account['id'], quota_day))
    if batch and not remaining and quota_day != schedule_store.now_wib().date().isoformat():
        schedule_store.finish_batch(conn, batch)
        batch = schedule_store.batch_for(conn, account['id'], runtime)
        quota_day = batch['quota_day']
        remaining = max(0, max_per_day - daily_post_count(conn, account['id'], quota_day))
    if mode != 'none' and remaining == 0:
        print('Batas video hari ini sudah tercapai; tidak membuka Chrome atau upload.')
        if batch:
            schedule_store.finish_batch(conn, batch)
        conn.close()
        return True
    if "--retest-content" in sys.argv:
        # Publish ulang video bekas memerlukan flag khusus di samping mode auto.
        permitted = account["publish_mode"] in ("auto", "manual_review", "schedule") and (
            bool(test_options) or mode == "manual" or (mode == "auto" and "--allow-retest-publish" in sys.argv)
        )
        if not cli_publish or not permitted:
            conn.close()
            raise SystemExit("Tes ulang memerlukan --publish. Mode auto juga memerlukan --allow-retest-publish.")
        try:
            retest_id = int(sys.argv[sys.argv.index("--retest-content") + 1])
        except (ValueError, IndexError):
            conn.close()
            raise SystemExit("--retest-content memerlukan ID konten numerik.")
        row = cur.execute(
            "SELECT p.id,p.folder_name,p.tiktok_product_id,c.id,c.file_path "
            "FROM contents c JOIN products p ON "
            "((c.source_folder IS NOT NULL AND c.source_folder=p.folder_path) OR (c.source_folder IS NULL AND c.product_id=p.id)) "
            "WHERE c.id=? AND p.account_id=? AND p.active=1",
            (retest_id, account["id"]),
        ).fetchone()
        if not row or not row[2] or not Path(row[4]).is_file():
            conn.close()
            raise SystemExit("Konten tes/produk/file tidak tersedia untuk akun ini.")
        validate_video(row[4])
        products = [row[:3]]
        retest_id_to_claim = prepare_retest(conn, account["id"], row[0], row[3])
        retest_content = True
        print(f"TES E2E ULANG: konten {retest_id}, satu {mode}; riwayat lama tetap ada.")
    else:
        if flat:
            first = cur.execute('SELECT id,folder_name,tiktok_product_id FROM products WHERE account_id=? AND active=1 ORDER BY sort_order LIMIT 1',(account["id"],)).fetchone()
            products = [first] * (remaining if mode != 'none' else 1) if first else []
        elif mode == 'schedule':
            available = cur.execute('SELECT id,folder_name,tiktok_product_id FROM products WHERE account_id=? AND active=1 AND tiktok_product_id IS NOT NULL ORDER BY sort_order', (account['id'],)).fetchall()
            # Setiap produk boleh menyumbang beberapa video dalam satu batch.
            products = available * remaining
        else:
            products = get_active_products(cur, account["id"])

    if mode == "none":
        products = products[:1]
        print("MODE: TEST (1 produk doang, gak beneran publish)\n")
    else:
        if mode != 'schedule' or flat:
            products = products[:remaining]
        label = 'SCHEDULE (upload berurutan)' if mode == 'schedule' else "FULL-AUTO (publish langsung)" if mode == "auto" else "AUTO-DRAFT (manual_review)"
        print(f"MODE: {label} -- max {max_per_day} video hari ini, "
              f"jendela mulai jam {WINDOW_START_HOUR:02d}:{WINDOW_START_MINUTE:02d}")
        if batch:
            print(f"Tanggal kuota batch: {quota_day}; jeda tayang {batch['interval_minutes']} menit, tanpa menunggu antar-upload.\n")
        else:
            print(f"Jeda {MIN_INTERVAL_MINUTES}-{MAX_INTERVAL_MINUTES} menit antar-post (random).\n")

    posted = 0
    batch_failed = False
    batch_browser = False
    owned_browser = False

    def cleanup_pre_submit_browser(state):
        nonlocal owned_browser
        if state=='unused' and owned_browser:
            if close_account_browser(account['cdp_port']) is not False:
                owned_browser=False
            else:
                print('Chrome bot belum tertutup setelah gagal sebelum submit; periksa log/browser.')

    with ExitStack() as batch_stack:
        for i, (product_id, folder_name, tiktok_product_id) in enumerate(products, start=1):
            if retest_content is None and (mode != "schedule" or not batch_browser):
                wait_for_window()
            if mode != 'none' and daily_post_count(conn, account['id'], quota_day) >= max_per_day:
                print('Batas video hari ini sudah tercapai; sisa antrean tidak diproses.')
                break
            print(f"=== [{i}/{len(products)}] Video akun {account_slug} ===" if flat else f"=== [{i}/{len(products)}] Produk: {folder_name} (ID: {tiktok_product_id}) ===")

            if not tiktok_product_id:
                print("  SKIP -- produk gak punya tiktok_product_id (kemungkinan active tapi belum diisi).\n")
                continue

            try:
                claim = claim_video(conn, account["id"], product_id, retest_id_to_claim)
            except ValueError as error:
                print(f"  STOP: {error}")
                batch_failed = True
                break
            if not claim:
                print("  SKIP -- tidak ada video unused yang tersedia (mungkin dikunci akun lain).\n")
                continue
            content_id, video_path = claim.content_id, claim.path
            if flat:
                product_id = claim.product_id
                tiktok_product_id = cur.execute('SELECT tiktok_product_id FROM content_assignments WHERE content_id=?',(content_id,)).fetchone()[0]
                folder_name = 'ID ' + tiktok_product_id
                print(f'  Pasangan tersimpan: video {content_id} → produk {tiktok_product_id}')
            print(f"  Klaim video {content_id} untuk {account_slug}: {Path(video_path).name}")
            caption = None
            if not batch:
                owned_browser = False
            try:
                validate_video(video_path)
                product_name_arg = folder_name if account["caption_style"] == "product_name" and not flat else None
                caption = generate_unique_caption(cur, account["id"], product_name_arg)
                if caption is None:
                    raise StepFailed("Caption generation failed")
                conn.execute("INSERT INTO captions_history (account_id, caption_text) VALUES (?, ?)",
                             (account["id"], caption))
                conn.commit()
                print(f'  Caption: "{caption}"')
                if mode == 'schedule' and not batch_browser:
                    batch_stack.enter_context(browser_slot(DB_PATH.parent.parent, account_slug, account['cdp_port'], runtime['max_parallel_browsers']))
                    batch_browser = True
                slot_context = nullcontext() if mode == 'schedule' else browser_slot(DB_PATH.parent.parent, account_slug, account['cdp_port'], runtime['max_parallel_browsers'])
                with slot_context:
                    schedule_context = {'reserve': lambda: schedule_store.reserve(conn, claim, batch)} if batch else None
                    if test_schedule:
                        target = datetime.fromisoformat(test_options['scheduled_at'])
                        schedule_context = {'reserve': lambda: schedule_store.reserve_test(conn, claim, target)}
                    def upload(before_submit):
                        nonlocal owned_browser
                        created = prepare_browser(DB_PATH.parent.parent, account_slug, account['cdp_port'])
                        owned_browser = owned_browser or created is True
                        return post_one_video(
                            account["cdp_port"], video_path, caption, tiktok_product_id, mode,
                            before_submit=before_submit,
                            **({'schedule_context': schedule_context} if schedule_context else {}),
                            product_link_name=runtime['product_link_name'],
                            expected_profile=Path(account['chrome_profile_path']) / runtime['chrome_profile_directory'],
                            music_context={'base': DB_PATH.parent.parent, 'account': account_slug,
                                           'content_id': content_id} if runtime['music_enabled'] else None,
                        )

                    def reset_browser():
                        if not owned_browser and browser_ready(account['cdp_port']):
                            raise StepFailed('Chrome dibuka manual; pemulihan tidak menutup browser pengguna. Periksa sebelum mencoba lagi.')
                        if browser_ready(account['cdp_port']) and close_account_browser(account['cdp_port']) is False:
                            raise StepFailed('Chrome belum bisa disiapkan ulang setelah sleep.')

                    success, error = run_with_wake_recovery(
                        WAKE_MONITOR, upload,
                        lambda: begin_submission(conn, claim, caption, daily_limit=max_per_day, **({'quota_day': quota_day, 'scheduled': True, 'test_scheduled':test_schedule} if mode == 'schedule' else {})), reset_browser,
                    )
                    if success:
                        if mode in ("auto", "manual", "schedule"):
                            finish_success(conn, claim, mode)
                            archive_after_success(conn, content_id)
                            posted += 1
                            held = cur.execute("SELECT 1 FROM contents WHERE claimed_by=? AND status IN ('claimed','submitting','uncertain') LIMIT 1", (account['id'],)).fetchone()
                            if not held and mode != "schedule" and owned_browser:
                                if close_account_browser(account['cdp_port']) is False:
                                    print('STOP: Chrome belum tertutup. Hasil video tersimpan; periksa browser sebelum melanjutkan.')
                                    batch_failed = True
                                    break
                        else:
                            release_preview(conn, claim)
                        print()
                    else:
                        state = finish_failure(conn, claim, caption, error)
                        cleanup_pre_submit_browser(state)
                        print(f"  GAGAL: {error}")
                        if state == "uncertain":
                            print("  VIDEO DITAHAN: hasil submit belum pasti; periksa TikTok sebelum retry.")
                        print("\n>>> STOP TOTAL -- sisa produk hari ini tidak diproses.")
                        batch_failed = True
                        break
            except BaseException as error:
                conn.rollback()
                # Hasil yang sudah selesai tidak diubah jika penutupan browser gagal.
                if cur.execute('SELECT status FROM contents WHERE id=?', (content_id,)).fetchone()[0] != 'used':
                    state=finish_failure(conn, claim, caption, str(error))
                    cleanup_pre_submit_browser(state)
                batch_failed = True
                if not isinstance(error, Exception):
                    raise
                print(f"  STOP TOTAL: {error}")
                break

            if mode in ("auto", "manual") and i < len(products):
                wait_minutes = random.randint(MIN_INTERVAL_MINUTES, MAX_INTERVAL_MINUTES)
                print(f"  Tunggu {wait_minutes} menit sebelum produk berikutnya...\n")
                wait_until(time.time() + wait_minutes * 60)

        if mode == 'schedule' and not batch_failed:
            if batch:
                schedule_store.finish_batch(conn, batch)
            if batch_browser and owned_browser and close_account_browser(account['cdp_port']) is False:
                print('Batch tersimpan; Chrome belum tertutup, periksa browser.')
                batch_failed = True
    conn.close()
    print(f"\n=== SELESAI. Total berhasil {'terjadwal' if mode == 'schedule' else 'simpan draft' if mode == 'manual' else 'publish'}: {posted} ===")
    return not batch_failed


def main():
    global WAKE_MONITOR
    parent=os.environ.get('TIKTOK_SUPERVISOR_PID','')
    monitor = WakeMonitor(supervisor_pid=int(parent) if parent.isdigit() and int(parent)>0 else None)
    WAKE_MONITOR = monitor
    monitor.start()
    try:
        return run_day()
    finally:
        monitor.stopped.set()
        WAKE_MONITOR = None


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
