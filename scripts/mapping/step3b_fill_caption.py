#!/usr/bin/env python3
"""
step3b_fill_caption.py
Mapping step 3b: klik ke caption box (Draft.js editor), clear existing text
(klik + Ctrl+A + Delete), lalu isi caption baru lewat clipboard + Ctrl+V.

Usage: python3 step3b_fill_caption.py
"""

import json
import time
import requests
import websocket

CDP_PORT = 9221
CLICK_X = 761
CLICK_Y = 348
CAPTION_TEXT = "Jangan sampai ketinggalan promonya, cek sekarang sebelum kehabisan!"
TIKTOK_ORIGIN = "https://www.tiktok.com"


def find_upload_tab(port):
    resp = requests.get(f"http://127.0.0.1:{port}/json")
    resp.raise_for_status()
    for tab in resp.json():
        if "tiktokstudio/upload" in tab.get("url", ""):
            return tab
    raise RuntimeError("Gak nemu tab tiktokstudio/upload yang kebuka.")


def send_cdp(ws, method, params=None, msg_id=1):
    payload = {"id": msg_id, "method": method, "params": params or {}}
    ws.send(json.dumps(payload))
    while True:
        raw = ws.recv()
        data = json.loads(raw)
        if data.get("id") == msg_id:
            return data


def click(ws, x, y, mid_start):
    send_cdp(ws, "Input.dispatchMouseEvent",
              {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1},
              msg_id=mid_start)
    send_cdp(ws, "Input.dispatchMouseEvent",
              {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1},
              msg_id=mid_start + 1)
    return mid_start + 2


def key_combo(ws, key, code, vkey, ctrl, mid_start):
    modifiers = 2 if ctrl else 0
    send_cdp(ws, "Input.dispatchKeyEvent",
              {"type": "keyDown", "modifiers": modifiers, "key": key, "code": code,
               "windowsVirtualKeyCode": vkey, "nativeVirtualKeyCode": vkey},
              msg_id=mid_start)
    send_cdp(ws, "Input.dispatchKeyEvent",
              {"type": "keyUp", "modifiers": modifiers, "key": key, "code": code,
               "windowsVirtualKeyCode": vkey, "nativeVirtualKeyCode": vkey},
              msg_id=mid_start + 1)
    return mid_start + 2


def main():
    tab = find_upload_tab(CDP_PORT)
    ws = websocket.create_connection(tab["webSocketDebuggerUrl"])
    mid = 1

    send_cdp(ws, "DOM.enable", msg_id=mid); mid += 1
    send_cdp(ws, "Input.enable", msg_id=mid); mid += 1

    print("Kasih izin clipboard buat origin TikTok...")
    send_cdp(ws, "Browser.grantPermissions",
             {"origin": TIKTOK_ORIGIN, "permissions": ["clipboardReadWrite", "clipboardSanitizedWrite"]},
             msg_id=mid); mid += 1

    print(f"Klik ke caption box ({CLICK_X}, {CLICK_Y})...")
    mid = click(ws, CLICK_X, CLICK_Y, mid)
    time.sleep(0.5)

    print("Ctrl+A (select all)...")
    mid = key_combo(ws, "a", "KeyA", 65, ctrl=True, mid_start=mid)
    time.sleep(0.3)

    print("Delete...")
    mid = key_combo(ws, "Delete", "Delete", 46, ctrl=False, mid_start=mid)
    time.sleep(0.3)

    print(f"Set clipboard: \"{CAPTION_TEXT}\"")
    clip_result = send_cdp(
        ws, "Runtime.evaluate",
        {"expression": f"navigator.clipboard.writeText({json.dumps(CAPTION_TEXT)})",
         "awaitPromise": True},
        msg_id=mid,
    )
    mid += 1
    if clip_result.get("result", {}).get("exceptionDetails"):
        print(f"GAGAL set clipboard: {clip_result}")
        ws.close()
        return
    time.sleep(0.3)

    print("Ctrl+V (paste)...")
    mid = key_combo(ws, "v", "KeyV", 86, ctrl=True, mid_start=mid)
    time.sleep(1)

    check = send_cdp(
        ws, "Runtime.evaluate",
        {"expression": "document.querySelector('.public-DraftEditor-content').innerText",
         "returnByValue": True},
        msg_id=mid,
    )
    current_text = check.get("result", {}).get("result", {}).get("value", "")
    ws.close()

    print(f"\nIsi caption box sekarang: \"{current_text}\"")
    print(">> Cek juga visual di layar -- captionnya beneran ke-paste bener? Ada duplikat/hashtag dobel?")


if __name__ == "__main__":
    main()
