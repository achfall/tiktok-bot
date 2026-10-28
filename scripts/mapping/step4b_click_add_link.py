#!/usr/bin/env python3
"""
step4b_click_add_link.py (v2)
Mapping step 4b: cari posisi live tombol "Add" (Add link) lalu langsung
klik dalam eksekusi yang sama (gak pakai koordinat basi dari scan lama).

Usage: python3 step4b_click_add_link.py
"""

import json
import time
import requests
import websocket

CDP_PORT = 9221

FIND_BTN_JS = """
(() => {
  const btns = Array.from(document.querySelectorAll('button'))
    .filter(b => (b.innerText || '').trim() === 'Add');
  if (btns.length === 0) return null;
  const btn = btns[btns.length - 1];
  const r = btn.getBoundingClientRect();
  return {
    x: Math.round(r.x + r.width / 2),
    y: Math.round(r.y + r.height / 2),
    count: btns.length,
  };
})()
"""

LIST_BUTTONS_JS = """
(() => {
  return Array.from(document.querySelectorAll('button'))
    .map((el, i) => {
      const r = el.getBoundingClientRect();
      return {
        index: i,
        text: (el.innerText || el.getAttribute('aria-label') || '').slice(0, 40),
        x: Math.round(r.x), y: Math.round(r.y),
        width: Math.round(r.width), height: Math.round(r.height),
        visible: r.width > 0 && r.height > 0,
      };
    })
    .filter(b => b.visible);
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


def main():
    tab = find_upload_tab(CDP_PORT)
    ws = websocket.create_connection(tab["webSocketDebuggerUrl"])

    print("Cari posisi live tombol Add link...")
    pos_result = send_cdp(ws, "Runtime.evaluate",
                           {"expression": FIND_BTN_JS, "returnByValue": True}, msg_id=1)
    pos = pos_result.get("result", {}).get("result", {}).get("value")

    if not pos:
        print("GAK KETEMU button dengan text persis 'Add'. Cek manual di layar,"
              " mungkin teksnya beda dari yang diasumsikan.")
        ws.close()
        return

    print(f"Ketemu {pos['count']} button 'Add', pakai yang terakhir -> posisi ({pos['x']},{pos['y']})")

    send_cdp(ws, "Input.dispatchMouseEvent",
              {"type": "mousePressed", "x": pos["x"], "y": pos["y"],
               "button": "left", "clickCount": 1}, msg_id=2)
    send_cdp(ws, "Input.dispatchMouseEvent",
              {"type": "mouseReleased", "x": pos["x"], "y": pos["y"],
               "button": "left", "clickCount": 1}, msg_id=3)

    print("Klik dikirim. Tunggu 1.5 detik...")
    time.sleep(1.5)

    result = send_cdp(ws, "Runtime.evaluate",
                       {"expression": LIST_BUTTONS_JS, "returnByValue": True}, msg_id=4)
    buttons = result.get("result", {}).get("result", {}).get("value", [])
    ws.close()

    print(f"\nButton visible setelah klik ({len(buttons)}):\n")
    for b in buttons:
        print(f"[{b['index']}] \"{b['text']}\"  pos=({b['x']},{b['y']})  size={b['width']}x{b['height']}")

    print("\n>> Cek layar: popup muncul sekarang?")


if __name__ == "__main__":
    main()
