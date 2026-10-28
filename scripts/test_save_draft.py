#!/usr/bin/env python3
"""
test_save_draft.py
Test klik "Save draft" (bukan Post) -- buat liat apa yang terjadi setelahnya
(redirect kemana, notifikasi apa) sebelum kita bikin verifikasi resminya.
Ambil produk aktif pertama + video unused pertama, TAPI JANGAN mark used
otomatis (ini test doang).

Usage: python3 test_save_draft.py <account_slug>
Contoh: python3 test_save_draft.py akun2
"""

import json
import sqlite3
import sys
import time
import requests
import websocket

DB_PATH = "/home/sintia/tiktok-bot/db/tiktok_bot.db"
OVERRIDE_TEXT = "Promo Tiktok Disini"
TEST_CAPTION = "Jangan sampai ketinggalan promonya, cek sekarang sebelum kehabisan!"
UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload"
SCROLL_SNIPPET = "el.scrollIntoView({block:'center',inline:'center'});"


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
    modifiers = 2 if ctrl else 0
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
    print(f"    [{label}] gak ketemu, tunggu {retry_wait}s coba lagi...")
    time.sleep(retry_wait)
    pos = eval_js(ws, expr, mid); mid += 1
    return pos, mid


def pick_test_item(account_slug):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, cdp_port FROM accounts WHERE slug=?", (account_slug,))
    account_id, cdp_port = cur.fetchone()
    cur.execute("""
        SELECT p.id, p.folder_name, p.tiktok_product_id, c.id, c.file_path
        FROM products p JOIN contents c ON c.product_id = p.id
        WHERE p.account_id=? AND p.active=1 AND c.status='unused'
        ORDER BY p.sort_order ASC, c.id ASC LIMIT 1
    """, (account_id,))
    row = cur.fetchone()
    conn.close()
    return {"cdp_port": cdp_port, "folder_name": row[1], "tiktok_product_id": row[2], "video_path": row[4]}


def main():
    account_slug = sys.argv[1]
    item = pick_test_item(account_slug)
    print(f"Produk test : {item['folder_name']} (ID: {item['tiktok_product_id']})")
    print(f"Video test  : {item['video_path']}\n")

    resp = requests.put(f"http://127.0.0.1:{item['cdp_port']}/json/new?{UPLOAD_URL}")
    tab = resp.json()
    ws = websocket.create_connection(tab["webSocketDebuggerUrl"])
    mid = 1
    send_cdp(ws, "Page.enable", msg_id=mid); mid += 1
    send_cdp(ws, "DOM.enable", msg_id=mid); mid += 1
    send_cdp(ws, "Input.enable", msg_id=mid); mid += 1
    send_cdp(ws, "Browser.grantPermissions",
             {"origin": "https://www.tiktok.com", "permissions": ["clipboardReadWrite", "clipboardSanitizedWrite"]},
             msg_id=mid); mid += 1

    print("[1-2] Load + upload video..."); time.sleep(4)
    doc = send_cdp(ws, "DOM.getDocument", msg_id=mid); mid += 1
    root_id = doc["result"]["root"]["nodeId"]
    q = send_cdp(ws, "DOM.querySelector", {"nodeId": root_id, "selector": "input[type='file']"}, msg_id=mid); mid += 1
    send_cdp(ws, "DOM.setFileInputFiles", {"files": [item["video_path"]], "nodeId": q["result"]["nodeId"]}, msg_id=mid); mid += 1
    time.sleep(6)

    print("[3] Caption...")
    cp = eval_js(ws, f"(()=>{{const el=document.querySelector('.public-DraftEditor-content');{SCROLL_SNIPPET}const r=el.getBoundingClientRect();return{{x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)}};}})()", mid); mid += 1
    mid = click(ws, cp["x"], cp["y"], mid); time.sleep(0.5)
    mid = key_combo(ws, "a", "KeyA", 65, True, mid); time.sleep(0.3)
    mid = key_combo(ws, "Delete", "Delete", 46, False, mid); time.sleep(0.3)
    send_cdp(ws, "Runtime.evaluate", {"expression": f"navigator.clipboard.writeText({json.dumps(TEST_CAPTION)})", "awaitPromise": True}, msg_id=mid); mid += 1
    time.sleep(0.4)
    mid = key_combo(ws, "v", "KeyV", 86, True, mid); time.sleep(2.5)

    print("[4-5] Add link + Next...")
    pos, mid = find_with_retry(ws, f"(()=>{{const b=Array.from(document.querySelectorAll('button')).filter(b=>(b.innerText||'').trim()==='Add');if(!b.length)return null;const el=b[b.length-1];{SCROLL_SNIPPET}const r=el.getBoundingClientRect();return{{x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)}};}})()", mid, "Add link")
    mid = click(ws, pos["x"], pos["y"], mid); time.sleep(2)
    pos, mid = find_with_retry(ws, f"(()=>{{const b=Array.from(document.querySelectorAll('button')).filter(b=>(b.innerText||'').trim()==='Next');if(!b.length)return null;const el=b[b.length-1];{SCROLL_SNIPPET}const r=el.getBoundingClientRect();return{{x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)}};}})()", mid, "Next")
    mid = click(ws, pos["x"], pos["y"], mid); time.sleep(4)

    toggle_pos = eval_js(ws, f"(()=>{{const b=Array.from(document.querySelectorAll('button')).filter(b=>(b.innerText||'').trim()==='Showcase products');if(!b.length)return null;const el=b[0];{SCROLL_SNIPPET}const r=el.getBoundingClientRect();return{{x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)}};}})()", mid); mid += 1
    if toggle_pos:
        print("    Toggle Showcase products ditemukan, klik...")
        mid = click(ws, toggle_pos["x"], toggle_pos["y"], mid); time.sleep(1.5)

    print(f"[6] Search ID {item['tiktok_product_id']}...")
    pos, mid = find_with_retry(ws, "(()=>{const el=document.querySelector('input[placeholder=\"Search products\"]');if(!el)return null;el.scrollIntoView({block:'center',inline:'center'});const r=el.getBoundingClientRect();return{x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)};})()", mid, "search")
    mid = click(ws, pos["x"], pos["y"], mid); time.sleep(0.4)
    send_cdp(ws, "Input.insertText", {"text": item["tiktok_product_id"]}, msg_id=mid); mid += 1
    time.sleep(0.4)
    mid = key_combo(ws, "Enter", "Enter", 13, False, mid); time.sleep(2)

    print("[7] Radio produk...")
    radio_expr = ("(()=>{const radios=Array.from(document.querySelectorAll('input[type=\"radio\"]'))"
                   ".filter(e=>e.getBoundingClientRect().width>0);"
                   "const candidates=radios.filter(e=>{let node=e,text='';"
                   "for(let l=0;l<4;l++){if(!node.parentElement)break;node=node.parentElement;"
                   "const t=(node.innerText||'').trim();if(t.length>text.length)text=t;}"
                   "return !text.includes('When to post');});"
                   "if(!candidates.length)return null;const el=candidates[0];"
                   "el.scrollIntoView({block:'center',inline:'center'});"
                   "const r=el.getBoundingClientRect();"
                   "return{x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)};})()")
    pos, mid = find_with_retry(ws, radio_expr, mid, "radio")
    mid = click(ws, pos["x"], pos["y"], mid); time.sleep(1)

    print("[8] Next...")
    pos, mid = find_with_retry(ws, f"(()=>{{const b=Array.from(document.querySelectorAll('button')).filter(b=>(b.innerText||'').trim()==='Next');if(!b.length)return null;const el=b[b.length-1];{SCROLL_SNIPPET}const r=el.getBoundingClientRect();return{{x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)}};}})()", mid, "Next2")
    mid = click(ws, pos["x"], pos["y"], mid); time.sleep(2)

    print("[9] Ganti teks produk...")
    field, mid = find_with_retry(ws, f"(()=>{{const i=Array.from(document.querySelectorAll('input[type=\"text\"]')).filter(e=>{{const r=e.getBoundingClientRect();return r.width>0&&r.y>300&&r.y<600&&r.x>500;}});if(!i.length)return null;const el=i[0];{SCROLL_SNIPPET}const r=el.getBoundingClientRect();return{{x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)}};}})()", mid, "name field")
    mid = click(ws, field["x"], field["y"], mid); time.sleep(0.5)
    mid = key_combo(ws, "a", "KeyA", 65, True, mid); time.sleep(0.3)
    mid = key_combo(ws, "Delete", "Delete", 46, False, mid); time.sleep(0.3)
    send_cdp(ws, "Input.insertText", {"text": OVERRIDE_TEXT}, msg_id=mid); mid += 1
    time.sleep(0.6)

    print("[10] Add (finalisasi link)...")
    add_btn, mid = find_with_retry(ws, f"(()=>{{const b=Array.from(document.querySelectorAll('button')).filter(b=>(b.innerText||'').trim()==='Add');if(!b.length)return null;const el=b[b.length-1];{SCROLL_SNIPPET}const r=el.getBoundingClientRect();return{{x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)}};}})()", mid, "Add finalize")
    mid = click(ws, add_btn["x"], add_btn["y"], mid); time.sleep(1.5)

    print("\n>>> READY_TO_POST. Sekarang klik 'Save draft' (BUKAN Post)...")
    draft_btn, mid = find_with_retry(ws, f"(()=>{{const b=Array.from(document.querySelectorAll('button')).filter(b=>(b.innerText||'').trim()==='Save draft'&&!b.disabled);if(!b.length)return null;const el=b[b.length-1];{SCROLL_SNIPPET}const r=el.getBoundingClientRect();return{{x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)}};}})()", mid, "Save draft btn")
    if not draft_btn:
        print("GAK KETEMU tombol Save draft. STOP.")
        ws.close()
        return
    print(f"Klik Save draft di ({draft_btn['x']},{draft_btn['y']})...")
    mid = click(ws, draft_btn["x"], draft_btn["y"], mid)

    print("\nTunggu 5 detik, terus laporin state halaman...")
    time.sleep(5)

    url = eval_js(ws, "location.href", mid); mid += 1
    print(f"URL sekarang: {url}")

    # cek ada toast/notifikasi/text yang mengandung kata "draft"
    notif = eval_js(ws, "(()=>{const els=Array.from(document.querySelectorAll('*')).filter(e=>e.children.length===0&&/draft/i.test(e.innerText||''));return els.slice(0,10).map(e=>(e.innerText||'').slice(0,100));})()", mid); mid += 1
    print(f"Elemen yang mengandung kata 'draft': {notif}")

    ws.close()
    print("\n>>> Cek juga manual di layar: ada notifikasi/toast yang muncul? Halaman-nya jadi kayak apa?")


if __name__ == "__main__":
    main()
