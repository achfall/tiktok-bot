#!/usr/bin/env python3
"""
step4d_search_product.py (v2)
Mapping step 4d: klik ke field "Search products", ketik product ID,
tekan Enter buat trigger search, lalu cek apakah muncul hasil match.
"""

import json
import time
import requests
import websocket

CDP_PORT = 9221
SEARCH_QUERY = "1735965391583675828"  # product ID test

FIND_INPUT_JS = """
(() => {
  const el = document.querySelector('input[placeholder="Search products"]');
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
})()
"""

LIST_RADIOS_JS = """
(() => {
  const radios = Array.from(document.querySelectorAll('input[type="radio"]'));
  return radios.map((el, i) => {
    const r = el.getBoundingClientRect();
    let labelText = '';
    let node = el.closest('label') || el.parentElement;
    if (node) labelText = (node.innerText || '').slice(0, 80);
    return {
      index: i,
      label: labelText,
      x: Math.round(r.x), y: Math.round(r.y),
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


def main():
    tab = find_upload_tab(CDP_PORT)
    ws = websocket.create_connection(tab["webSocketDebuggerUrl"])

    print("Cari posisi live field 'Search products'...")
    pos_result = send_cdp(ws, "Runtime.evaluate",
                           {"expression": FIND_INPUT_JS, "returnByValue": True}, msg_id=1)
    pos = pos_result.get("result", {}).get("result", {}).get("value")

    if not pos:
        print("GAK KETEMU input 'Search products'. Cek manual apa masih di popup Showcase products.")
        ws.close()
        return

    print(f"Klik field search di ({pos['x']},{pos['y']})...")
    send_cdp(ws, "Input.dispatchMouseEvent",
              {"type": "mousePressed", "x": pos["x"], "y": pos["y"],
               "button": "left", "clickCount": 1}, msg_id=2)
    send_cdp(ws, "Input.dispatchMouseEvent",
              {"type": "mouseReleased", "x": pos["x"], "y": pos["y"],
               "button": "left", "clickCount": 1}, msg_id=3)
    time.sleep(0.3)

    print(f"Ketik query: \"{SEARCH_QUERY}\"")
    send_cdp(ws, "Input.insertText", {"text": SEARCH_QUERY}, msg_id=4)
    time.sleep(0.3)

    print("Tekan Enter buat trigger search...")
    send_cdp(ws, "Input.dispatchKeyEvent",
              {"type": "keyDown", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13}, msg_id=5)
    send_cdp(ws, "Input.dispatchKeyEvent",
              {"type": "keyUp", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13}, msg_id=6)

    print("Tunggu 2 detik buat hasil search muncul...")
    time.sleep(2)

    result = send_cdp(ws, "Runtime.evaluate",
                       {"expression": LIST_RADIOS_JS, "returnByValue": True}, msg_id=7)
    radios = result.get("result", {}).get("result", {}).get("value", [])
    ws.close()

    print(f"\nKetemu {len(radios)} radio button hasil search:\n")
    for r in radios:
        print(f"[{r['index']}] label=\"{r['label']}\"  pos=({r['x']},{r['y']})")

    print("\n>> Cek layar: ada hasil produk yang match muncul, atau 'no results'/kosong?")


if __name__ == "__main__":
    main()
