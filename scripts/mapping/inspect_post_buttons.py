#!/usr/bin/env python3
"""Cari semua button dengan teks persis 'Post' di halaman yang lagi kebuka."""
import json
import requests
import websocket

CDP_PORT = 9221

INSPECT_JS = """
(() => {
  const btns = Array.from(document.querySelectorAll('button'))
    .filter(b => (b.innerText || '').trim() === 'Post');
  return btns.map((el, i) => {
    const r = el.getBoundingClientRect();
    return {
      index: i,
      disabled: el.disabled,
      x: Math.round(r.x), y: Math.round(r.y),
      width: Math.round(r.width), height: Math.round(r.height),
      visible: r.width > 0 && r.height > 0,
      classList: el.className.slice(0, 80),
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
    result = send_cdp(ws, "Runtime.evaluate",
                       {"expression": INSPECT_JS, "returnByValue": True}, msg_id=1)
    btns = result.get("result", {}).get("result", {}).get("value", [])
    ws.close()

    print(f"Ketemu {len(btns)} button dengan teks persis 'Post':\n")
    for b in btns:
        print(f"[{b['index']}] disabled={b['disabled']} visible={b['visible']} pos=({b['x']},{b['y']}) size={b['width']}x{b['height']}")
        print(f"    class: {b['classList']}\n")


if __name__ == "__main__":
    main()
