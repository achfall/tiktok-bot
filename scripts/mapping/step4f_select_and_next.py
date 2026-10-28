#!/usr/bin/env python3
"""
step4f_select_and_next.py
Mapping step 4f: klik radio produk hasil search, cek tombol Next aktif,
klik Next, lalu scan input field yang muncul setelahnya (harusnya field
nama produk yang mau diganti dengan teks hardcode).
"""

import json
import time
import requests
import websocket

CDP_PORT = 9221

FIND_RADIO_JS = """
(() => {
  const el = Array.from(document.querySelectorAll('input[type="radio"]'))
    .find(el => {
      const r = el.getBoundingClientRect();
      return r.width > 0 && Math.abs(r.x - 271) < 15;
    });
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
})()
"""

FIND_NEXT_JS = """
(() => {
  const btns = Array.from(document.querySelectorAll('button'))
    .filter(b => (b.innerText || '').trim() === 'Next');
  if (btns.length === 0) return null;
  const btn = btns[btns.length - 1];
  const r = btn.getBoundingClientRect();
  return {
    x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2),
    disabled: btn.disabled,
  };
})()
"""

LIST_INPUTS_JS = """
(() => {
  return Array.from(document.querySelectorAll('input, textarea')).map((el, i) => {
    const r = el.getBoundingClientRect();
    return {
      index: i, tag: el.tagName, type: el.type || '',
      placeholder: el.placeholder || '', value: (el.value || '').slice(0, 60),
      x: Math.round(r.x), y: Math.round(r.y),
      width: Math.round(r.width), height: Math.round(r.height),
      visible: r.width > 0 && r.height > 0,
    };
  }).filter(b => b.visible);
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


def main():
    tab = find_upload_tab(CDP_PORT)
    ws = websocket.create_connection(tab["webSocketDebuggerUrl"])
    mid = 1

    print("Cari posisi live radio produk...")
    r = send_cdp(ws, "Runtime.evaluate", {"expression": FIND_RADIO_JS, "returnByValue": True}, msg_id=mid); mid += 1
    radio_pos = r.get("result", {}).get("result", {}).get("value")
    if not radio_pos:
        print("GAK KETEMU radio produk. Cek manual masih di hasil search?")
        ws.close(); return

    print(f"Klik radio di ({radio_pos['x']},{radio_pos['y']})...")
    mid = click(ws, radio_pos["x"], radio_pos["y"], mid)
    time.sleep(0.8)

    print("Cek status tombol Next...")
    r = send_cdp(ws, "Runtime.evaluate", {"expression": FIND_NEXT_JS, "returnByValue": True}, msg_id=mid); mid += 1
    next_info = r.get("result", {}).get("result", {}).get("value")
    if not next_info:
        print("GAK KETEMU tombol Next.")
        ws.close(); return

    print(f"Next -> pos=({next_info['x']},{next_info['y']})  disabled={next_info['disabled']}")

    if next_info["disabled"]:
        print("Next masih disabled -- STOP, gak lanjut klik. Cek manual kenapa radio belum keregister.")
        ws.close()
        return

    print("Next aktif, klik...")
    mid = click(ws, next_info["x"], next_info["y"], mid)
    time.sleep(1.5)

    print("Scan input/textarea yang muncul setelah Next...")
    r = send_cdp(ws, "Runtime.evaluate", {"expression": LIST_INPUTS_JS, "returnByValue": True}, msg_id=mid); mid += 1
    inputs = r.get("result", {}).get("result", {}).get("value", [])
    ws.close()

    print(f"\nKetemu {len(inputs)} input/textarea visible:\n")
    for inp in inputs:
        print(f"[{inp['index']}] <{inp['tag']}> type={inp['type']} placeholder=\"{inp['placeholder']}\" value=\"{inp['value']}\"")
        print(f"    pos=({inp['x']},{inp['y']}) size={inp['width']}x{inp['height']}\n")

    print(">> Cek layar: field nama produk yang mau diedit ada di index berapa?")


if __name__ == "__main__":
    main()
