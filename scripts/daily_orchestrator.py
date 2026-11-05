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
  python3 daily_orchestrator.py akun1 --publish               # mode sesuai DB (auto/manual)
  python3 daily_orchestrator.py akun1 --publish --limit 5     # batasi max 5 video hari itu
"""

import json
import random
import sqlite3
import sys
import time
from datetime import datetime
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
from browser_lifecycle import browser_slot, prepare_browser, browser_ready
from music_favorites import apply_favorite_music
from archive_used import archive_after_success  # noqa: E402
from content_queue import (scan_account, claim_video, begin_submission, finish_success,
                           finish_failure, release_preview, prepare_retest)  # noqa: E402

OVERRIDE_TEXT = defaults()['product_link_name']
UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload"
STEP_DELAY = 1.0
MIN_INTERVAL_MINUTES = 35  # jeda dinamis, biar polanya gak keliatan robotik
MAX_INTERVAL_MINUTES = 50
WINDOW_START_HOUR = 10  # jendela posting: 10.00 - tengah malam (24.00/00.00)
WINDOW_START_MINUTE = 0
DEFAULT_MAX_PER_DAY = 15  # sesuai kesepakatan turun ke 10-15/hari buat versi automasi

SCROLL_SNIPPET = "el.scrollIntoView({block:'center',inline:'center'});"


def step(msg):
    print(msg)
    time.sleep(STEP_DELAY)


def send_cdp(ws, method, params=None, msg_id=1):
    payload = {"id": msg_id, "method": method, "params": params or {}}
    ws.send(json.dumps(payload))
    while True:
        raw = ws.recv()
        data = json.loads(raw)
        if data.get("id") == msg_id:
            return data


def click(ws, x, y, mid):
    send_cdp(ws, "Input.dispatchMouseEvent",
              {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1}, msg_id=mid)
    send_cdp(ws, "Input.dispatchMouseEvent",
              {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1}, msg_id=mid + 1)
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
    return r.get("result", {}).get("result", {}).get("value")


def find_with_retry(ws, expr, mid, label, retry_wait=2.0):
    pos = eval_js(ws, expr, mid); mid += 1
    if pos:
        return pos, mid
    print(f"    [{label}] gak ketemu di percobaan pertama, tunggu {retry_wait}s terus coba 1x lagi...")
    time.sleep(retry_wait)
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
        time.sleep(wait_s)


class StepFailed(Exception):
    def __init__(self, message):
        self.message = message
        super().__init__(message)



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


def wait_for_submission(ws, mode, mid, attempts=45):
    confirmed = False
    url = None
    for _ in range(attempts):
        time.sleep(1)
        url = eval_js(ws, "location.href", mid)
        mid += 1
        parsed = urlparse(url or "")
        content = parsed.hostname in ("www.tiktok.com", "tiktok.com") and parsed.path.rstrip("/") == "/tiktokstudio/content"
        draft = parse_qs(parsed.query).get("tab") == ["draft"]
        if content and (not draft if mode == "auto" else draft):
            return True, url, mid
        if mode == "auto" and not confirmed:
            confirmed, mid = confirm_pending_post(ws, mid)
    return False, url, mid


def post_one_video(cdp_port, video_path, caption_text, tiktok_product_id, mode, before_submit=None, product_link_name=None, music_context=None):
    """Jalanin 1 video full pipeline. mode: 'none' | 'auto' | 'manual'.
    Return (success: bool, error_message: str|None)."""
    resp = requests.put(f"http://127.0.0.1:{cdp_port}/json/new?{UPLOAD_URL}")
    resp.raise_for_status()
    tab = resp.json()
    tab_id = tab["id"]
    ws = websocket.create_connection(tab["webSocketDebuggerUrl"])
    mid = 1

    try:
        send_cdp(ws, "Page.enable", msg_id=mid); mid += 1
        send_cdp(ws, "DOM.enable", msg_id=mid); mid += 1
        send_cdp(ws, "Input.enable", msg_id=mid); mid += 1
        send_cdp(ws, "Browser.grantPermissions",
                 {"origin": "https://www.tiktok.com", "permissions": ["clipboardReadWrite", "clipboardSanitizedWrite"]},
                 msg_id=mid); mid += 1

        step("  [1] Tunggu halaman upload load...")
        time.sleep(4)

        step("  [2] Upload video...")
        input_node_id = None
        for attempt in range(2):  # coba, kalau gagal tunggu lebih lama terus coba 1x lagi
            doc = send_cdp(ws, "DOM.getDocument", msg_id=mid); mid += 1
            root_id = doc["result"]["root"]["nodeId"]
            q = send_cdp(ws, "DOM.querySelector", {"nodeId": root_id, "selector": "input[type='file']"}, msg_id=mid); mid += 1
            input_node_id = q["result"].get("nodeId")
            if input_node_id:
                break
            if attempt == 0:
                print("    [file input] gak ketemu di percobaan pertama, tunggu 4s terus coba 1x lagi...")
                time.sleep(4)
        if not input_node_id:
            raise StepFailed("File input not found")
        send_cdp(ws, "DOM.setFileInputFiles", {"files": [video_path], "nodeId": input_node_id}, msg_id=mid); mid += 1
        time.sleep(6)

        step("  [3] Isi caption...")
        mid = fill_caption(ws, caption_text, mid)

        step("  [4] Klik Add link...")
        pos, mid = find_with_retry(ws, f"(()=>{{const b=Array.from(document.querySelectorAll('button')).filter(b=>(b.innerText||'').trim()==='Add');if(!b.length)return null;const el=b[b.length-1];{SCROLL_SNIPPET}const r=el.getBoundingClientRect();return{{x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)}};}})()", mid, "Add link btn")
        if not pos:
            raise StepFailed("Add link button not found")
        mid = click(ws, pos["x"], pos["y"], mid)
        time.sleep(2)

        step("  [5] Klik Next...")
        pos, mid = find_with_retry(ws, f"(()=>{{const b=Array.from(document.querySelectorAll('button')).filter(b=>(b.innerText||'').trim()==='Next');if(!b.length)return null;const el=b[b.length-1];{SCROLL_SNIPPET}const r=el.getBoundingClientRect();return{{x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)}};}})()", mid, "Next btn (popup 1)")
        if not pos:
            raise StepFailed("Next button not found")
        mid = click(ws, pos["x"], pos["y"], mid)
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
                requests.get(f"http://127.0.0.1:{cdp_port}/json/close/{tab_id}")
            except Exception:
                pass
            ws.close()
            return True, None

        if mode == "auto":
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
        if before_submit is not None:
            before_submit()
        mid = click(ws, action_btn["x"], action_btn["y"], mid)

        step("  [12] Verifikasi redirect...")
        success, url, mid = wait_for_submission(ws, mode, mid)

        ws.close()
        if success:
            print(f"  BERHASIL {action_label}! URL: {url}")
            try:
                requests.get(f"http://127.0.0.1:{cdp_port}/json/close/{tab_id}")
            except Exception:
                pass
            return True, None
        else:
            return False, f"Submission not verified after timeout (last url: {url}); check TikTok before retrying"

    except StepFailed as e:
        ws.close()
        return False, e.message
    except Exception as e:
        ws.close()
        return False, f"Unexpected error: {e}"


def get_account(cur, account_slug):
    cur.execute("SELECT id, cdp_port, publish_mode, caption_style FROM accounts WHERE slug=?", (account_slug,))
    row = cur.fetchone()
    if not row:
        raise RuntimeError(f"GAK KETEMU akun '{account_slug}'.")
    return {"id": row[0], "cdp_port": row[1], "publish_mode": row[2], "caption_style": row[3]}


def get_active_products(cur, account_id):
    """Produk aktif yang BELUM sukses publish hari ini (cek dari posts_log)."""
    cur.execute(
        """SELECT id, folder_name, tiktok_product_id FROM products
           WHERE account_id=? AND active=1
           AND id NOT IN (
               SELECT product_id FROM posts_log
               WHERE account_id=? AND (status IN ('submitting','uncertain') OR (status IN ('success','draft') AND DATE(published_at)=DATE('now')))
           )
           ORDER BY sort_order ASC""",
        (account_id, account_id),
    )
    return cur.fetchall()


def close_account_browser(cdp_port):
    """Tutup seluruh Chrome khusus akun secara normal, tanpa kill proses."""
    ws = None
    try:
        response = requests.get(f'http://127.0.0.1:{cdp_port}/json/version', timeout=5)
        response.raise_for_status()
        endpoint = response.json()['webSocketDebuggerUrl']
        parsed = urlparse(endpoint)
        if parsed.scheme != 'ws' or parsed.hostname not in ('127.0.0.1', 'localhost') or parsed.port != cdp_port:
            raise ValueError('Endpoint browser tidak sesuai port akun')
        ws = websocket.create_connection(endpoint, timeout=5)
        ws.send(json.dumps({'id': 1, 'method': 'Browser.close'}))
        for _ in range(40):
            if not browser_ready(cdp_port):
                print('Chrome khusus akun ditutup; video telah terverifikasi.')
                return True
            time.sleep(.25)
        print('Peringatan: Chrome masih menutup; slot belum tersedia untuk akun lain.')
        return False
    except Exception as error:
        print(f'Peringatan: Chrome belum bisa ditutup otomatis ({type(error).__name__}). Hasil posting tetap tersimpan.')
        return False
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass  # Chrome bisa memutus socket sesudah Browser.close.


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 daily_orchestrator.py <account_slug> [--publish] [--limit N]")
        sys.exit(1)

    account_slug = sys.argv[1]
    from bot_settings import effective_settings
    runtime = effective_settings(DB_PATH.parent.parent, account_slug)
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
        max_per_day = int(sys.argv[sys.argv.index("--limit") + 1])

    conn = sqlite3.connect(DB_PATH, timeout=30)
    cur = conn.cursor()

    account = get_account(cur, account_slug)
    from account_sources import source as account_source
    flat = account_source(conn, account["id"])

    force_test_draft = "--test-draft" in sys.argv
    if force_test_draft and (not cli_publish or "--retest-content" not in sys.argv):
        conn.close()
        raise SystemExit("--test-draft hanya untuk --publish --retest-content <id>.")
    if force_test_draft:
        mode = "manual"
        print("TES DRAFT: aksi akhir Save draft; konfigurasi publish akun tidak diubah.")
    elif not cli_publish:
        mode = "none"
    elif account["publish_mode"] == "auto":
        mode = "auto"
    else:
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
    if "--retest-content" in sys.argv:
        # Publish ulang video bekas memerlukan flag khusus di samping mode auto.
        permitted = account["publish_mode"] in ("auto", "manual_review") and (
            mode == "manual" or (mode == "auto" and "--allow-retest-publish" in sys.argv)
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
        products = [row[:3]]
        retest_id_to_claim = prepare_retest(conn, account["id"], row[0], row[3])
        retest_content = True
        max_per_day = 1
        print(f"TES E2E ULANG: konten {retest_id}, satu {'POST PUBLIK' if mode == 'auto' else 'draft baru'}; riwayat lama tetap ada.")
    else:
        if flat:
            done = cur.execute("SELECT COUNT(*) FROM posts_log WHERE account_id=? AND status IN ('success','draft') AND DATE(published_at,'localtime')=DATE('now','localtime')", (account["id"],)).fetchone()[0]
            first = cur.execute('SELECT id,folder_name,tiktok_product_id FROM products WHERE account_id=? AND active=1 ORDER BY sort_order LIMIT 1',(account["id"],)).fetchone()
            products = [first] * max(0, max_per_day - done) if first else []
        else:
            products = get_active_products(cur, account["id"])

    if mode == "none":
        products = products[:1]
        print("MODE: TEST (1 produk doang, gak beneran publish)\n")
    else:
        products = products[:max_per_day]
        label = "FULL-AUTO (publish langsung)" if mode == "auto" else "AUTO-DRAFT (manual_review)"
        print(f"MODE: {label} -- max {max_per_day} produk hari ini, "
              f"jeda {MIN_INTERVAL_MINUTES}-{MAX_INTERVAL_MINUTES} menit antar-post (random), "
              f"jendela mulai jam {WINDOW_START_HOUR:02d}:{WINDOW_START_MINUTE:02d}\n")

    posted = 0
    for i, (product_id, folder_name, tiktok_product_id) in enumerate(products, start=1):
        if retest_content is None:
            wait_for_window()
        print(f"=== [{i}/{len(products)}] Video akun {account_slug} ===" if flat else f"=== [{i}/{len(products)}] Produk: {folder_name} (ID: {tiktok_product_id}) ===")

        if not tiktok_product_id:
            print("  SKIP -- produk gak punya tiktok_product_id (kemungkinan active tapi belum diisi).\n")
            continue

        try:
            claim = claim_video(conn, account["id"], product_id, retest_id_to_claim)
        except ValueError as error:
            print(f"  STOP: {error}")
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
        try:
            product_name_arg = folder_name if account["caption_style"] == "product_name" and not flat else None
            caption = generate_unique_caption(cur, account["id"], product_name_arg)
            if caption is None:
                raise StepFailed("Caption generation failed")
            conn.execute("INSERT INTO captions_history (account_id, caption_text) VALUES (?, ?)",
                         (account["id"], caption))
            conn.commit()
            print(f'  Caption: "{caption}"')
            with browser_slot(DB_PATH.parent.parent, account_slug, account['cdp_port'], runtime['max_parallel_browsers']):
                prepare_browser(DB_PATH.parent.parent, account_slug, account['cdp_port'])
                success, error = post_one_video(
                    account["cdp_port"], video_path, caption, tiktok_product_id, mode,
                    before_submit=lambda: begin_submission(conn, claim, caption),
                    product_link_name=runtime['product_link_name'],
                    music_context={'base': DB_PATH.parent.parent, 'account': account_slug,
                                   'content_id': content_id} if runtime['music_enabled'] else None,
                )
                if success:
                    if mode in ("auto", "manual"):
                        finish_success(conn, claim, mode)
                        archive_after_success(conn, content_id)
                        posted += 1
                        held = cur.execute("SELECT 1 FROM contents WHERE claimed_by=? AND status IN ('claimed','submitting','uncertain') LIMIT 1", (account['id'],)).fetchone()
                        if not held:
                            if close_account_browser(account['cdp_port']) is False:
                                print('STOP: Chrome belum tertutup. Hasil video tersimpan; periksa browser sebelum melanjutkan.')
                                break
                    else:
                        release_preview(conn, claim)
                    print()
                else:
                    state = finish_failure(conn, claim, caption, error)
                    print(f"  GAGAL: {error}")
                    if state == "uncertain":
                        print("  VIDEO DITAHAN: hasil submit belum pasti; periksa TikTok sebelum retry.")
                    print("\n>>> STOP TOTAL -- sisa produk hari ini tidak diproses.")
                    break
        except BaseException as error:
            conn.rollback()
            # Hasil yang sudah selesai tidak diubah jika penutupan browser gagal.
            if cur.execute('SELECT status FROM contents WHERE id=?', (content_id,)).fetchone()[0] != 'used':
                finish_failure(conn, claim, caption, str(error))
            if not isinstance(error, Exception):
                raise
            print(f"  STOP TOTAL: {error}")
            break

        if mode in ("auto", "manual") and i < len(products):
            wait_minutes = random.randint(MIN_INTERVAL_MINUTES, MAX_INTERVAL_MINUTES)
            print(f"  Tunggu {wait_minutes} menit sebelum produk berikutnya...\n")
            time.sleep(wait_minutes * 60)

    conn.close()
    print(f"\n=== SELESAI. Total berhasil {'simpan draft' if mode == 'manual' else 'publish'}: {posted} ===")


if __name__ == "__main__":
    main()
