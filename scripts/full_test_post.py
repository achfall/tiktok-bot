#!/usr/bin/env python3
"""
full_test_post.py
Test end-to-end: navigate -> upload video -> caption -> add link (product
by ID) -> [Post, kalau --publish].

Ambil produk aktif pertama (sort_order terkecil) + video unused pertamanya
dari database. Default DRY RUN (berhenti sebelum klik Post, cuma laporan
READY_TO_POST). Tambahin --publish buat beneran klik Post.

Usage:
  python3 full_test_post.py akun1            # dry run
  python3 full_test_post.py akun1 --publish  # beneran publish
"""

import json
import sqlite3
import sys
import time
import requests
import websocket

CDP_PORT = 9221
DB_PATH = "/home/sintia/tiktok-bot/db/tiktok_bot.db"
OVERRIDE_TEXT = "Promo Tiktok Disini"
TEST_CAPTION = "Jangan sampai ketinggalan promonya, cek sekarang sebelum kehabisan!"
UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload"
STEP_DELAY = 1.0

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
    print(f"    [{label}] gak ketemu di percobaan pertama, tunggu {retry_wait}s terus coba 1x lagi...")
    time.sleep(retry_wait)
    pos = eval_js(ws, expr, mid); mid += 1
    return pos, mid


def pick_test_item(account_slug):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id FROM accounts WHERE slug=?", (account_slug,))
    account_id = cur.fetchone()[0]

    cur.execute("""
        SELECT p.id, p.folder_name, p.tiktok_product_id, c.id, c.file_path
        FROM products p
        JOIN contents c ON c.product_id = p.id
        WHERE p.account_id=? AND p.active=1 AND c.status='unused'
        ORDER BY p.sort_order ASC, c.id ASC
        LIMIT 1
    """, (account_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        raise RuntimeError("Gak ada produk aktif + video unused yang bisa dipakai buat test.")
    return {
        "account_id": account_id,
        "product_id": row[0],
        "folder_name": row[1],
        "tiktok_product_id": row[2],
        "content_id": row[3],
        "video_path": row[4],
    }


def mark_used_and_log(item, status, error_message=None):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    if status == "success":
        cur.execute("UPDATE contents SET status='used', used_at=datetime('now') WHERE id=?",
                    (item["content_id"],))
    cur.execute("""
        INSERT INTO posts_log (account_id, product_id, content_id, caption_text, status, error_message, published_at)
        VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
    """, (item["account_id"], item["product_id"], item["content_id"], TEST_CAPTION, status, error_message))
    conn.commit()
    conn.close()


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 full_test_post.py <account_slug> [--publish]")
        sys.exit(1)
    account_slug = sys.argv[1]
    do_publish = "--publish" in sys.argv[2:]

    item = pick_test_item(account_slug)
    print(f"Produk test : {item['folder_name']} (ID: {item['tiktok_product_id']})")
    print(f"Video test  : {item['video_path']}")
    print(f"Mode        : {'PUBLISH BENERAN' if do_publish else 'DRY RUN (gak klik Post)'}\n")

    resp = requests.put(f"http://127.0.0.1:{CDP_PORT}/json/new?{UPLOAD_URL}")
    resp.raise_for_status()
    tab = resp.json()
    ws = websocket.create_connection(tab["webSocketDebuggerUrl"])
    mid = 1

    send_cdp(ws, "Page.enable", msg_id=mid); mid += 1
    send_cdp(ws, "DOM.enable", msg_id=mid); mid += 1
    send_cdp(ws, "Input.enable", msg_id=mid); mid += 1
    send_cdp(ws, "Browser.grantPermissions",
             {"origin": "https://www.tiktok.com", "permissions": ["clipboardReadWrite", "clipboardSanitizedWrite"]},
             msg_id=mid); mid += 1

    step("[1] Tunggu halaman upload load...")
    time.sleep(4)

    step("[2] Upload video...")
    doc = send_cdp(ws, "DOM.getDocument", msg_id=mid); mid += 1
    root_id = doc["result"]["root"]["nodeId"]
    q = send_cdp(ws, "DOM.querySelector", {"nodeId": root_id, "selector": "input[type='file']"}, msg_id=mid); mid += 1
    input_node_id = q["result"]["nodeId"]
    send_cdp(ws, "DOM.setFileInputFiles", {"files": [item["video_path"]], "nodeId": input_node_id}, msg_id=mid); mid += 1
    time.sleep(6)

    step("[3] Isi caption...")
    caption_pos = eval_js(ws, f"(()=>{{const el=document.querySelector('.public-DraftEditor-content');{SCROLL_SNIPPET}const r=el.getBoundingClientRect();return{{x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)}};}})()", mid); mid += 1
    mid = click(ws, caption_pos["x"], caption_pos["y"], mid)
    time.sleep(0.5)
    mid = key_combo(ws, "a", "KeyA", 65, True, mid); time.sleep(0.3)
    mid = key_combo(ws, "Delete", "Delete", 46, False, mid); time.sleep(0.3)
    send_cdp(ws, "Runtime.evaluate",
             {"expression": f"navigator.clipboard.writeText({json.dumps(TEST_CAPTION)})", "awaitPromise": True},
             msg_id=mid); mid += 1
    time.sleep(0.4)
    mid = key_combo(ws, "v", "KeyV", 86, True, mid)
    time.sleep(2.5)

    step("[4] Klik Add link...")
    pos, mid = find_with_retry(ws, f"(()=>{{const b=Array.from(document.querySelectorAll('button')).filter(b=>(b.innerText||'').trim()==='Add');if(!b.length)return null;const el=b[b.length-1];{SCROLL_SNIPPET}const r=el.getBoundingClientRect();return{{x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)}};}})()", mid, "Add link btn")
    if not pos:
        print("GAK KETEMU tombol Add link. STOP."); mark_used_and_log(item, "failed", "Add link button not found"); ws.close(); return
    mid = click(ws, pos["x"], pos["y"], mid)
    time.sleep(2)

    step("[5] Klik Next...")
    pos, mid = find_with_retry(ws, f"(()=>{{const b=Array.from(document.querySelectorAll('button')).filter(b=>(b.innerText||'').trim()==='Next');if(!b.length)return null;const el=b[b.length-1];{SCROLL_SNIPPET}const r=el.getBoundingClientRect();return{{x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)}};}})()", mid, "Next btn (popup 1)")
    if not pos:
        print("GAK KETEMU tombol Next. STOP."); mark_used_and_log(item, "failed", "Next button not found"); ws.close(); return
    mid = click(ws, pos["x"], pos["y"], mid)
    time.sleep(4)  # kasih waktu list produk showcase render penuh (lebih lama di koneksi lambat)

    step(f"[6] Search produk by ID: {item['tiktok_product_id']}...")
    pos, mid = find_with_retry(ws, f"(()=>{{const el=document.querySelector('input[placeholder=\"Search products\"]');if(!el)return null;{SCROLL_SNIPPET}const r=el.getBoundingClientRect();return{{x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)}};}})()", mid, "search field")
    if not pos:
        print("GAK KETEMU search field. STOP."); mark_used_and_log(item, "failed", "Search field not found"); ws.close(); return
    mid = click(ws, pos["x"], pos["y"], mid)
    time.sleep(0.4)
    send_cdp(ws, "Input.insertText", {"text": item["tiktok_product_id"]}, msg_id=mid); mid += 1
    time.sleep(0.4)
    mid = key_combo(ws, "Enter", "Enter", 13, False, mid)
    time.sleep(2)

    step("[7] Klik radio produk...")
    pos, mid = find_with_retry(ws, f"(()=>{{const el=Array.from(document.querySelectorAll('input[type=\"radio\"]')).find(e=>{{const r=e.getBoundingClientRect();return r.width>0&&Math.abs(r.x-271)<15;}});if(!el)return null;{SCROLL_SNIPPET}const r=el.getBoundingClientRect();return{{x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)}};}})()", mid, "product radio")
    if not pos:
        print("GAK KETEMU hasil search produk. STOP -- cek ID produk bener/salah."); mark_used_and_log(item, "failed", "Product search returned no results"); ws.close(); return
    mid = click(ws, pos["x"], pos["y"], mid)
    time.sleep(1)

    step("[8] Klik Next (setelah pilih produk)...")
    next_info, mid = find_with_retry(ws, f"(()=>{{const b=Array.from(document.querySelectorAll('button')).filter(b=>(b.innerText||'').trim()==='Next');if(!b.length)return null;const el=b[b.length-1];{SCROLL_SNIPPET}const r=el.getBoundingClientRect();return{{x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2),disabled:el.disabled}};}})()", mid, "Next btn (popup 2)")
    if not next_info or next_info["disabled"]:
        print("Next gak ketemu/disabled. STOP."); mark_used_and_log(item, "failed", "Next disabled after product select"); ws.close(); return
    mid = click(ws, next_info["x"], next_info["y"], mid)
    time.sleep(2)

    step(f"[9] Ganti nama produk jadi: \"{OVERRIDE_TEXT}\"...")
    field, mid = find_with_retry(ws, f"(()=>{{const i=Array.from(document.querySelectorAll('input[type=\"text\"]')).filter(e=>{{const r=e.getBoundingClientRect();return r.width>0&&r.y>300&&r.y<600&&r.x>500;}});if(!i.length)return null;const el=i[0];{SCROLL_SNIPPET}const r=el.getBoundingClientRect();return{{x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)}};}})()", mid, "product name field")
    if not field:
        print("GAK KETEMU field nama produk. STOP."); mark_used_and_log(item, "failed", "Product name field not found"); ws.close(); return
    mid = click(ws, field["x"], field["y"], mid)
    time.sleep(0.5)
    mid = key_combo(ws, "a", "KeyA", 65, True, mid); time.sleep(0.3)
    mid = key_combo(ws, "Delete", "Delete", 46, False, mid); time.sleep(0.3)
    send_cdp(ws, "Input.insertText", {"text": OVERRIDE_TEXT}, msg_id=mid); mid += 1
    time.sleep(0.6)

    step("[10] Klik Add (finalisasi link)...")
    add_btn, mid = find_with_retry(ws, f"(()=>{{const b=Array.from(document.querySelectorAll('button')).filter(b=>(b.innerText||'').trim()==='Add');if(!b.length)return null;const el=b[b.length-1];{SCROLL_SNIPPET}const r=el.getBoundingClientRect();return{{x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2),disabled:el.disabled}};}})()", mid, "Add (finalize) btn")
    if not add_btn or add_btn["disabled"]:
        print("Add gak ketemu/disabled. STOP."); mark_used_and_log(item, "failed", "Add button disabled"); ws.close(); return
    mid = click(ws, add_btn["x"], add_btn["y"], mid)
    time.sleep(1.5)

    print("\n>>> READY_TO_POST -- link produk udah ke-attach.")

    if not do_publish:
        print(">>> DRY RUN selesai, gak klik Post. Cek dulu visual di layar.")
        print(">>> Kalau semua bener, jalanin ulang dengan flag --publish buat beneran publish.")
        ws.close()
        return

    step("\n[11] Cari & klik tombol Post (exact text match)...")
    post_info, mid = find_with_retry(ws, f"(()=>{{const b=Array.from(document.querySelectorAll('button')).filter(b=>(b.innerText||'').trim()==='Post'&&!b.disabled);if(!b.length)return null;const el=b[b.length-1];{SCROLL_SNIPPET}const r=el.getBoundingClientRect();return{{x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2),text:'Post'}};}})()", mid, "Post btn")
    if not post_info:
        print("GAK KETEMU tombol Post. STOP."); mark_used_and_log(item, "failed", "Post button not found"); ws.close(); return
    print(f"Klik tombol \"{post_info['text']}\" di ({post_info['x']},{post_info['y']})...")
    mid = click(ws, post_info["x"], post_info["y"], mid)

    step("[12] Verifikasi redirect ke tiktokstudio/content (polling max 20 detik)...")
    success = False
    for _ in range(20):
        time.sleep(1)
        url = eval_js(ws, "location.href", mid); mid += 1
        if url and "tiktokstudio/content" in url:
            success = True
            break

    ws.close()

    if success:
        print(f"\nBERHASIL PUBLISH! URL: {url}")
        mark_used_and_log(item, "success")
    else:
        print(f"\nGAK ADA REDIRECT ke tiktokstudio/content dalam 20 detik. URL terakhir: {url}")
        print("Cek manual di layar -- mungkin masih proses, atau ada error.")
        mark_used_and_log(item, "failed", "No redirect to tiktokstudio/content within timeout")


if __name__ == "__main__":
    main()
