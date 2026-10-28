#!/usr/bin/env python3
"""
step4g_edit_name_and_add.py
Mapping step 4g: klik field nama produk (index 10 dari scan sebelumnya),
clear isinya, ganti dengan teks hardcode, lalu klik tombol Add buat
nyelesain proses attach link (BUKAN klik Post -- belum publish).
"""

import json
import time
import requests
import websocket

CDP_PORT = 9221
OVERRIDE_TEXT = "Promo Tiktok Disini"

FIND_NAME_FIELD_JS = """
(() => {
  const inputs = Array.from(document.querySelectorAll('input[type="text"]'))
    .filter(el => {
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.y > 300 && r.y < 600 && r.x > 500;
    });
  if (inputs.length === 0) return null;
  const el = inputs[0];
  const r = el.getBoundingClientRect();
  return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2), value: el.value };
})()
"""

FIND_ADD_BTN_JS = """
(() => {
  const btns = Array.from(document.querySelectorAll('button'))
    .filter(b => (b.innerText || '').trim() === 'Add');
  if (btns.length === 0) return null;
  const btn = btns[btns.length - 1];
  const r = btn.getBoundingClientRect();
  return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2), disabled: btn.disabled };
})()
"""


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


def main():
    tab = find_upload_tab(CDP_PORT)
    ws = websocket.create_connection(tab["webSocketDebuggerUrl"])
    mid = 1

    print("Cari posisi live field nama produk...")
    r = send_cdp(ws, "Runtime.evaluate", {"expression": FIND_NAME_FIELD_JS, "returnByValue": True}, msg_id=mid); mid += 1
    field = r.get("result", {}).get("result", {}).get("value")
    if not field:
        print("GAK KETEMU field nama produk. Cek manual posisinya.")
        ws.close(); return

    print(f"Ketemu field, value sekarang: \"{field['value']}\" di ({field['x']},{field['y']})")

    print("Klik field...")
    mid = click(ws, field["x"], field["y"], mid)
    time.sleep(0.4)

    print("Ctrl+A...")
    mid = key_combo(ws, "a", "KeyA", 65, True, mid)
    time.sleep(0.2)

    print("Delete...")
    mid = key_combo(ws, "Delete", "Delete", 46, False, mid)
    time.sleep(0.2)

    print(f"Ketik teks baru: \"{OVERRIDE_TEXT}\"")
    send_cdp(ws, "Input.insertText", {"text": OVERRIDE_TEXT}, msg_id=mid); mid += 1
    time.sleep(0.5)

    print("Cari tombol Add...")
    r = send_cdp(ws, "Runtime.evaluate", {"expression": FIND_ADD_BTN_JS, "returnByValue": True}, msg_id=mid); mid += 1
    add_btn = r.get("result", {}).get("result", {}).get("value")
    if not add_btn:
        print("GAK KETEMU tombol Add. STOP -- cek manual.")
        ws.close(); return

    print(f"Add -> pos=({add_btn['x']},{add_btn['y']}) disabled={add_btn['disabled']}")
    if add_btn["disabled"]:
        print("Add masih disabled -- STOP, gak lanjut klik.")
        ws.close(); return

    print("Klik Add...")
    mid = click(ws, add_btn["x"], add_btn["y"], mid)
    time.sleep(1.5)

    print("\n>> SELESAI (belum klik Post). Cek layar: popup ketutup? Link produk"
          " keliatan attached di halaman upload dengan teks override-nya?")

    ws.close()


if __name__ == "__main__":
    main()
