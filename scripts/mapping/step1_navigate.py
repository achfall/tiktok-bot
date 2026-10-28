#!/usr/bin/env python3
"""
step1_navigate.py
Mapping step 1: connect ke Chrome CDP (akun1, port 9221) dan buka halaman
upload TikTok Studio. Jalankan lalu laporin apa yang keliatan di window Chrome.

Usage: python3 step1_navigate.py
"""

import json
import time
import requests
import websocket

CDP_PORT = 9221
UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload"


def get_ws_url_new_tab(port, url):
    """Buka tab baru langsung ke URL target, return websocket debugger URL-nya."""
    resp = requests.put(f"http://127.0.0.1:{port}/json/new?{url}")
    resp.raise_for_status()
    tab = resp.json()
    return tab["webSocketDebuggerUrl"]


def send_cdp(ws, method, params=None, msg_id=1):
    payload = {"id": msg_id, "method": method, "params": params or {}}
    ws.send(json.dumps(payload))
    while True:
        raw = ws.recv()
        data = json.loads(raw)
        if data.get("id") == msg_id:
            return data


def main():
    print(f"Connecting ke Chrome CDP di port {CDP_PORT}...")
    ws_url = get_ws_url_new_tab(CDP_PORT, UPLOAD_URL)
    print(f"Tab baru dibuka.\nWebSocket: {ws_url}")

    ws = websocket.create_connection(ws_url)
    send_cdp(ws, "Page.enable", msg_id=1)

    print("Tunggu 5 detik buat load...")
    time.sleep(5)

    result = send_cdp(
        ws,
        "Runtime.evaluate",
        {"expression": "({title: document.title, url: location.href})", "returnByValue": True},
        msg_id=2,
    )
    value = result.get("result", {}).get("result", {}).get("value", {})
    print(f"\nTitle : {value.get('title')}")
    print(f"URL   : {value.get('url')}")

    ws.close()
    print("\n>> Cek window Chrome akun1 sekarang. Laporin: halaman upload beneran"
          " kebuka? Diminta login lagi? Ada redirect ke halaman lain?")


if __name__ == "__main__":
    main()
