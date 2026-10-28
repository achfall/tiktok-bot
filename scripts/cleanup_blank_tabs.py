#!/usr/bin/env python3
"""
cleanup_blank_tabs.py
Tutup semua tab dengan URL kosong/blank di Chrome instance tertentu -- sisa
dari percobaan yang gagal, gak pernah ketutup otomatis.

Usage: python3 cleanup_blank_tabs.py <cdp_port>
Contoh: python3 cleanup_blank_tabs.py 9223
"""
import sys
import requests

def main():
    port = sys.argv[1]
    resp = requests.get(f"http://127.0.0.1:{port}/json")
    tabs = resp.json()

    blank_tabs = [t for t in tabs if not t.get("url", "").strip() and t.get("type") == "page"]
    print(f"Ketemu {len(blank_tabs)} tab blank dari {len(tabs)} total tab.")

    closed = 0
    for t in blank_tabs:
        try:
            r = requests.get(f"http://127.0.0.1:{port}/json/close/{t['id']}")
            if r.status_code == 200:
                closed += 1
        except Exception as e:
            print(f"Gagal nutup {t['id']}: {e}")

    print(f"Berhasil ditutup: {closed}/{len(blank_tabs)}")


if __name__ == "__main__":
    main()
