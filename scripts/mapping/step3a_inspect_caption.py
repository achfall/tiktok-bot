#!/usr/bin/env python3
"""
step3a_inspect_caption.py
Mapping step 3a: cari semua elemen contenteditable di halaman upload TikTok
Studio, print info-nya (posisi, teks, class) biar kita bisa identifikasi
mana yang caption box -- belum ngapa-ngapain ke halaman.

Usage: python3 step3a_inspect_caption.py
"""

import json
import requests
import websocket

CDP_PORT = 9221

INSPECT_JS = """
(() => {
  const els = Array.from(document.querySelectorAll('[contenteditable="true"]'));
  return els.map((el, i) => {
    const r = el.getBoundingClientRect();
    return {
      index: i,
      tag: el.tagName,
      className: (el.className || '').toString().slice(0, 80),
      text: (el.innerText || '').slice(0, 50),
      x: Math.round(r.x),
      y: Math.round(r.y),
      width: Math.round(r.width),
      height: Math.round(r.height),
    };
  });
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
        ws,
        "Runtime.evaluate",
        {"expression": INSPECT_JS, "returnByValue": True},
        msg_id=1,
    )
    elements = result.get("result", {}).get("result", {}).get("value", [])
    ws.close()

    if not elements:
        print("Gak ketemu elemen contenteditable sama sekali di halaman ini.")
        return

    print(f"Ketemu {len(elements)} elemen contenteditable:\n")
    for el in elements:
        print(f"[{el['index']}] <{el['tag']}> class=\"{el['className']}\"")
        print(f"     posisi: x={el['x']}, y={el['y']}, size={el['width']}x{el['height']}")
        print(f"     text preview: \"{el['text']}\"\n")

    print(">> Laporin: index berapa yang keliatan visualnya kayak caption box di layar?")


if __name__ == "__main__":
    main()
