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

import requests
import websocket

BASE_DIR = Path.home() / "tiktok-bot"
DB_PATH = BASE_DIR / "db" / "tiktok_bot.db"
SCRIPTS_DIR = BASE_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
from caption_generator import generate_unique_caption  # noqa: E402

OVERRIDE_TEXT = "Promo Tiktok Disini"
UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload"
STEP_DELAY = 1.0
MIN_INTERVAL_MINUTES = 35  # jeda dinamis, biar polanya gak keliatan robotik
MAX_INTERVAL_MINUTES = 50
WINDOW_START_HOUR = 10  # jendela posting: 10.00 - tengah malam (24.00/00.00)
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


def wait_for_window():
    """Kalau sekarang masih sebelum jam 10 pagi, tunggu sampai jam 10 baru lanjut."""
    now = datetime.now()
    if now.hour < WINDOW_START_HOUR:
        target = now.replace(hour=WINDOW_START_HOUR, minute=0, second=0, microsecond=0)
        wait_s = (target - now).total_seconds()
        print(f"Di luar jendela posting (mulai jam {WINDOW_START_HOUR:02d}:00) -- "
              f"nunggu {wait_s/60:.0f} menit sampai jendela buka...")
        time.sleep(wait_s)


class StepFailed(Exception):
    def __init__(self, message):
        self.message = message
        super().__init__(message)


def post_one_video(cdp_port, video_path, caption_text, tiktok_product_id, mode):
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
        caption_pos = eval_js(ws, f"(()=>{{const el=document.querySelector('.public-DraftEditor-content');if(!el)return null;{SCROLL_SNIPPET}const r=el.getBoundingClientRect();return{{x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)}};}})()", mid); mid += 1
        if not caption_pos:
            raise StepFailed("Caption box not found")
        mid = click(ws, caption_pos["x"], caption_pos["y"], mid)
        time.sleep(0.5)
        mid = key_combo(ws, "a", "KeyA", 65, True, mid); time.sleep(0.3)
        mid = key_combo(ws, "Delete", "Delete", 46, False, mid); time.sleep(0.3)
        send_cdp(ws, "Runtime.evaluate",
                 {"expression": f"navigator.clipboard.writeText({json.dumps(caption_text)})", "awaitPromise": True},
                 msg_id=mid); mid += 1
        time.sleep(0.4)
        mid = key_combo(ws, "v", "KeyV", 86, True, mid)
        time.sleep(2.5)

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

        step(f"  [9] Ganti nama produk jadi: \"{OVERRIDE_TEXT}\"...")
        field, mid = find_with_retry(ws, f"(()=>{{const i=Array.from(document.querySelectorAll('input[type=\"text\"]')).filter(e=>{{const r=e.getBoundingClientRect();return r.width>0&&r.y>300&&r.y<600;}});if(!i.length)return null;const el=i[0];{SCROLL_SNIPPET}const r=el.getBoundingClientRect();return{{x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)}};}})()", mid, "product name field")
        if not field:
            raise StepFailed("Product name field not found")
        mid = click(ws, field["x"], field["y"], mid)
        time.sleep(0.5)
        mid = key_combo(ws, "a", "KeyA", 65, True, mid); time.sleep(0.3)
        mid = key_combo(ws, "Delete", "Delete", 46, False, mid); time.sleep(0.3)
        send_cdp(ws, "Input.insertText", {"text": OVERRIDE_TEXT}, msg_id=mid); mid += 1
        time.sleep(0.6)

        step("  [10] Klik Add (finalisasi link)...")
        add_btn, mid = find_with_retry(ws, f"(()=>{{const b=Array.from(document.querySelectorAll('button')).filter(b=>(b.innerText||'').trim()==='Add');if(!b.length)return null;const el=b[b.length-1];{SCROLL_SNIPPET}const r=el.getBoundingClientRect();return{{x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2),disabled:el.disabled}};}})()", mid, "Add (finalize) btn")
        if not add_btn or add_btn["disabled"]:
            raise StepFailed("Add button disabled")
        mid = click(ws, add_btn["x"], add_btn["y"], mid)
        time.sleep(1.5)

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

        mid = click(ws, action_btn["x"], action_btn["y"], mid)

        step("  [12] Verifikasi redirect...")
        success = False
        url = None
        for _ in range(20):
            time.sleep(1)
            url = eval_js(ws, "location.href", mid); mid += 1
            if url and success_marker in url:
                success = True
                break

        ws.close()
        if success:
            print(f"  BERHASIL {action_label}! URL: {url}")
            try:
                requests.get(f"http://127.0.0.1:{cdp_port}/json/close/{tab_id}")
            except Exception:
                pass
            return True, None
        else:
            return False, f"No redirect containing '{success_marker}' within timeout (last url: {url})"

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
               WHERE account_id=? AND status IN ('success','draft') AND DATE(published_at)=DATE('now')
           )
           ORDER BY sort_order ASC""",
        (account_id, account_id),
    )
    return cur.fetchall()


def get_next_unused_content(cur, product_id):
    cur.execute(
        "SELECT id, file_path FROM contents WHERE product_id=? AND status='unused' ORDER BY id ASC LIMIT 1",
        (product_id,),
    )
    return cur.fetchone()


def mark_used(conn, content_id):
    conn.execute("UPDATE contents SET status='used', used_at=datetime('now') WHERE id=?", (content_id,))
    conn.commit()


def log_post(conn, account_id, product_id, content_id, caption_text, status, error_message=None):
    conn.execute(
        """INSERT INTO posts_log (account_id, product_id, content_id, caption_text, status, error_message, published_at)
           VALUES (?, ?, ?, ?, ?, ?, datetime('now'))""",
        (account_id, product_id, content_id, caption_text, status, error_message),
    )
    conn.commit()


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 daily_orchestrator.py <account_slug> [--publish] [--limit N]")
        sys.exit(1)

    account_slug = sys.argv[1]
    cli_publish = "--publish" in sys.argv
    max_per_day = DEFAULT_MAX_PER_DAY
    if "--limit" in sys.argv:
        max_per_day = int(sys.argv[sys.argv.index("--limit") + 1])

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    account = get_account(cur, account_slug)

    if not cli_publish:
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

    products = get_active_products(cur, account["id"])

    if mode == "none":
        products = products[:1]
        print("MODE: TEST (1 produk doang, gak beneran publish)\n")
    else:
        products = products[:max_per_day]
        label = "FULL-AUTO (publish langsung)" if mode == "auto" else "AUTO-DRAFT (manual_review)"
        print(f"MODE: {label} -- max {max_per_day} produk hari ini, "
              f"jeda {MIN_INTERVAL_MINUTES}-{MAX_INTERVAL_MINUTES} menit antar-post (random), "
              f"jendela mulai jam {WINDOW_START_HOUR:02d}:00\n")

    posted = 0
    for i, (product_id, folder_name, tiktok_product_id) in enumerate(products, start=1):
        wait_for_window()
        print(f"=== [{i}/{len(products)}] Produk: {folder_name} (ID: {tiktok_product_id}) ===")

        if not tiktok_product_id:
            print("  SKIP -- produk gak punya tiktok_product_id (kemungkinan active tapi belum diisi).\n")
            continue

        content = get_next_unused_content(cur, product_id)
        if not content:
            print("  SKIP -- gak ada video unused tersisa buat produk ini.\n")
            continue
        content_id, video_path = content

        product_name_arg = folder_name if account["caption_style"] == "product_name" else None
        caption = generate_unique_caption(cur, account["id"], product_name_arg)
        if caption is None:
            print("  STOP TOTAL -- caption generator gagal (Gemini + DeepSeek dua-duanya gagal).")
            log_post(conn, account["id"], product_id, content_id, None, "failed", "Caption generation failed")
            break
        conn.execute("INSERT INTO captions_history (account_id, caption_text) VALUES (?, ?)",
                     (account["id"], caption))
        conn.commit()
        print(f"  Caption: \"{caption}\"")

        success, error = post_one_video(account["cdp_port"], video_path, caption, tiktok_product_id, mode)

        if success:
            if mode in ("auto", "manual"):
                mark_used(conn, content_id)
                log_status = "success" if mode == "auto" else "draft"
                log_post(conn, account["id"], product_id, content_id, caption, log_status)
                posted += 1
            print()
        else:
            print(f"  GAGAL: {error}")
            log_post(conn, account["id"], product_id, content_id, caption, "failed", error)
            print("\n>>> STOP TOTAL -- sisa produk hari ini TIDAK diproses (sesuai retry policy).")
            break

        if mode in ("auto", "manual") and i < len(products):
            wait_minutes = random.randint(MIN_INTERVAL_MINUTES, MAX_INTERVAL_MINUTES)
            print(f"  Tunggu {wait_minutes} menit sebelum produk berikutnya...\n")
            time.sleep(wait_minutes * 60)

    conn.close()
    print(f"\n=== SELESAI. Total berhasil publish: {posted} ===")


if __name__ == "__main__":
    main()
