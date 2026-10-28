#!/usr/bin/env python3
"""
step4e_verify_product.py
Mapping step 4e: ambil teks lengkap di sekitar radio button hasil search
produk (x~271), buat verifikasi nama produk sebelum diklik. Belum ngeklik
apa-apa.
"""

import json
import requests
import websocket

CDP_PORT = 9221

INSPECT_JS = """
(() => {
  const radios = Array.from(document.querySelectorAll('input[type="radio"]'))
    .filter(el => {
      const r = el.getBoundingClientRect();
      return r.width > 0 && Math.abs(r.x - 271) < 15;
    });
  return radios.map((el, i) => {
    const r = el.getBoundingClientRect();
    let node = el;
    let text = '';
    for (let level = 0; level < 4; level++) {
      if (!node.parentElement) break;
      node = node.parentElement;
      const t = (node.innerText || '').trim();
      if (t.length > text.length) text = t;
    }
    return {
      index: i,
      x: Math.round(r.x), y: Math.round(r.y),
      checked: el.checked,
      text: text.slice(0, 200),
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
    radios = result.get("result", {}).get("result", {}).get("value", [])
    ws.close()

    print(f"Ketemu {len(radios)} radio produk hasil search:\n")
    for r in radios:
        print(f"[{r['index']}] pos=({r['x']},{r['y']}) checked={r['checked']}")
        print(f"    text: \"{r['text']}\"\n")


if __name__ == "__main__":
    main()
