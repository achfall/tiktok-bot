#!/usr/bin/env python3
"""
step4c_click_next.py
Mapping step 4c: cari posisi live tombol "Next" di popup Add Link, klik,
lalu list semua button + input yang muncul setelahnya (harusnya toggle
My Shop / Showcase products).

Usage: python3 step4c_click_next.py
"""

import json
import time
import requests
import websocket

CDP_PORT = 9221

FIND_BTN_JS = """
(() => {
  const btns = Array.from(document.querySelectorAll('button'))
    .filter(b => (b.innerText || '').trim() === 'Next');
  if (btns.length === 0) return null;
  const btn = btns[btns.length - 1];
  const r = btn.getBoundingClientRect();
  return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2), count: btns.length };
})()
"""

LIST_ELEMENTS_JS = """
(() => {
  const buttons = Array.from(document.querySelectorAll('button')).map((el, i) => {
    const r = el.getBoundingClientRect();
    return { kind: 'button', index: i,
      text: (el.innerText || el.getAttribute('aria-label') || '').slice(0, 40),
      x: Math.round(r.x), y: Math.round(r.y),
      width: Math.round(r.width), height: Math.round(r.height),
      visible: r.width > 0 && r.height > 0 };
  }).filter(b => b.visible);

  const inputs = Array.from(document.querySelectorAll('input')).map((el, i) => {
    const r = el.getBoundingClientRect();
    return { kind: 'input', index: i,
      placeholder: el.placeholder || '', type: el.type,
      x: Math.round(r.x), y: Math.round(r.y),
      width: Math.round(r.width), height: Math.round(r.height),
      visible: r.width > 0 && r.height > 0 };
  }).filter(b => b.visible);

  const toggles = Array.from(document.querySelectorAll('[role="switch"], [role="tab"], [role="radio"]')).map((el, i) => {
    const r = el.getBoundingClientRect();
    return { kind: el.getAttribute('role'), index: i,
      text: (el.innerText || el.getAttribute('aria-label') || '').slice(0, 40),
      x: Math.round(r.x), y: Math.round(r.y),
      width: Math.round(r.width), height: Math.round(r.height),
      visible: r.width > 0 && r.height > 0 };
  }).filter(b => b.visible);

  return { buttons, inputs, toggles };
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

    print("Cari posisi live tombol Next...")
    pos_result = send_cdp(ws, "Runtime.evaluate",
                           {"expression": FIND_BTN_JS, "returnByValue": True}, msg_id=1)
    pos = pos_result.get("result", {}).get("result", {}).get("value")

    if not pos:
        print("GAK KETEMU button 'Next'. Cek manual dulu apa popup-nya masih kebuka.")
        ws.close()
        return

    print(f"Ketemu {pos['count']} button 'Next' -> posisi ({pos['x']},{pos['y']})")

    send_cdp(ws, "Input.dispatchMouseEvent",
              {"type": "mousePressed", "x": pos["x"], "y": pos["y"],
               "button": "left", "clickCount": 1}, msg_id=2)
    send_cdp(ws, "Input.dispatchMouseEvent",
              {"type": "mouseReleased", "x": pos["x"], "y": pos["y"],
               "button": "left", "clickCount": 1}, msg_id=3)

    print("Klik dikirim. Tunggu 1.5 detik...")
    time.sleep(1.5)

    result = send_cdp(ws, "Runtime.evaluate",
                       {"expression": LIST_ELEMENTS_JS, "returnByValue": True}, msg_id=4)
    data = result.get("result", {}).get("result", {}).get("value", {})
    ws.close()

    print(f"\n--- BUTTONS ({len(data.get('buttons', []))}) ---")
    for b in data.get("buttons", []):
        print(f"[{b['index']}] \"{b['text']}\"  pos=({b['x']},{b['y']})  size={b['width']}x{b['height']}")

    print(f"\n--- INPUTS ({len(data.get('inputs', []))}) ---")
    for b in data.get("inputs", []):
        print(f"[{b['index']}] placeholder=\"{b['placeholder']}\" type={b['type']}  pos=({b['x']},{b['y']})  size={b['width']}x{b['height']}")

    print(f"\n--- TOGGLES/TABS/RADIOS ({len(data.get('toggles', []))}) ---")
    for b in data.get("toggles", []):
        print(f"[{b['kind']}][{b['index']}] \"{b['text']}\"  pos=({b['x']},{b['y']})  size={b['width']}x{b['height']}")

    print("\n>> Cek layar: toggle My Shop / Showcase products beneran muncul?")


if __name__ == "__main__":
    main()
