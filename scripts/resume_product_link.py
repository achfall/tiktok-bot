#!/usr/bin/env python3
"""Lanjutkan dialog Product name pada satu tab; tanpa upload/Post/Save draft."""
import requests
import websocket
from daily_orchestrator import (
    DB_PATH, PRODUCT_NAME_FIELD_JS, attach_product_link, eval_js, StepFailed,
)
from bot_settings import effective_settings


def connect(tab):
    return websocket.create_connection(tab['webSocketDebuggerUrl'], timeout=15,
                                       origin='http://127.0.0.1:9221')


def main():
    response = requests.get('http://127.0.0.1:9221/json', timeout=5)
    response.raise_for_status()
    candidates = []
    for tab in response.json():
        if tab.get('type') != 'page' or 'tiktok.com/tiktokstudio/upload' not in tab.get('url', ''):
            continue
        ws = connect(tab)
        try:
            if eval_js(ws, PRODUCT_NAME_FIELD_JS, 1):
                candidates.append(tab)
        finally:
            ws.close()
    if len(candidates) != 1:
        raise SystemExit(f'Ditemukan {len(candidates)} tab dengan kolom Product name. Tidak ada teks/tombol diubah; sisakan satu dialog nama produk.')
    ws = connect(candidates[0])
    try:
        attach_product_link(ws, 1, effective_settings(DB_PATH.parent.parent,'akun1')['product_link_name'])
        print('Selesai: nama diverifikasi dan Add diklik; dialog tertutup.')
        print('Cek link produk pada layar. Tab tetap terbuka; Post/Save draft tidak diklik.')
    except StepFailed as error:
        raise SystemExit(f'Berhenti: {error}')
    finally:
        ws.close()


if __name__ == '__main__':
    main()
