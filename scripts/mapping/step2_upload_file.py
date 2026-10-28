#!/usr/bin/env python3
"""
step2_upload_file.py
Mapping step 2: cari tab yang lagi kebuka di halaman TikTok Studio upload,
lalu set file video ke hidden file input pakai DOM.setFileInputFiles
(bukan drag-drop).

Usage: python3 step2_upload_file.py
"""

import json
import time
import requests
import websocket

CDP_PORT = 9221
VIDEO_PATH = "/home/sintia/tiktok-bot/test-assets/1.mp4"


def find_upload_tab(port):
    resp = requests.get(f"http://127.0.0.1:{port}/json")
    resp.raise_for_status()
    tabs = resp.json()
    for tab in tabs:
        if "tiktokstudio/upload" in tab.get("url", ""):
            return tab
    raise RuntimeError(
        "Gak nemu tab yang lagi di halaman tiktokstudio/upload. "
        "Pastiin tab-nya masih kebuka (dari step1_navigate.py)."
    )


def send_cdp(ws, method, params=None, msg_id=1):
    payload = {"id": msg_id, "method": method, "params": params or {}}
    ws.send(json.dumps(payload))
    while True:
        raw = ws.recv()
        data = json.loads(raw)
        if data.get("id") == msg_id:
            return data


def main():
    print("Cari tab TikTok Studio upload yang lagi kebuka...")
    tab = find_upload_tab(CDP_PORT)
    print(f"Ketemu tab: {tab['url']}")

    ws = websocket.create_connection(tab["webSocketDebuggerUrl"])

    send_cdp(ws, "DOM.enable", msg_id=1)

    doc = send_cdp(ws, "DOM.getDocument", msg_id=2)
    root_node_id = doc["result"]["root"]["nodeId"]

    print("Cari elemen <input type='file'>...")
    query = send_cdp(
        ws,
        "DOM.querySelector",
        {"nodeId": root_node_id, "selector": "input[type='file']"},
        msg_id=3,
    )
    input_node_id = query.get("result", {}).get("nodeId")

    if not input_node_id:
        print("GAK KETEMU input[type='file'] di halaman ini.")
        print("Kemungkinan selector-nya beda / ada di dalam iframe.")
        ws.close()
        return

    print(f"Ketemu input file (nodeId={input_node_id}). Set file: {VIDEO_PATH}")
    result = send_cdp(
        ws,
        "DOM.setFileInputFiles",
        {"files": [VIDEO_PATH], "nodeId": input_node_id},
        msg_id=4,
    )
    print(f"Hasil set file: {result}")

    print("Tunggu 5 detik buat liat reaksi UI (progress upload, dst)...")
    time.sleep(5)

    ws.close()
    print("\n>> Cek window Chrome akun1 sekarang. Laporin: video ke-upload beneran?"
          " Ada progress bar? Ada error? Atau gak ada reaksi sama sekali?")


if __name__ == "__main__":
    main()
