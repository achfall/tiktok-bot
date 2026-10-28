#!/usr/bin/env python3
"""
step4a_inspect_buttons.py
Mapping step 4a: list semua <button> di halaman upload TikTok Studio
(teks, posisi) buat identifikasi tombol "+ Add" link.

Usage: python3 step4a_inspect_buttons.py
"""

import json
import requests
import websocket

CDP_PORT = 9221

INSPECT_JS = """
(() => {
  const els = Array.from(document.querySelectorAll('button'));
  return els
    .map((el, i) => {
      const r = el.getBoundingClientRect();
      return {
        index: i,
        text: (el.innerText || el.getAttribute('aria-label') || '').slice(0, 40),
        x: Math.round(r.x),
        y: Math.round(r.y),
        width: Math.round(r.width),
        height: Math.round(r.height),
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

    result = send_cdp(
        ws, "Runtime.evaluate",
        {"expression": INSPECT_JS, "returnByValue": True},
        msg_id=1,
    )
    buttons = result.get("result", {}).get("result", {}).get("value", [])
    ws.close()

    if not buttons:
        print("Gak ketemu button visible sama sekali.")
        return

    print(f"Ketemu {len(buttons)} button visible:\n")
    for b in buttons:
        print(f"[{b['index']}] \"{b['text']}\"  pos=({b['x']},{b['y']})  size={b['width']}x{b['height']}")

    print("\n>> Laporin: index berapa yang teksnya/posisinya kayak tombol \"+ Add\" (link)?")


if __name__ == "__main__":
    main()
