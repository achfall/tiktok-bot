#!/usr/bin/env python3
"""
caption_generator.py
Generate caption promo, unik tiap kali, pakai Gemini sebagai percobaan utama,
DeepSeek sebagai fallback kalau Gemini gagal. Cek juga ke captions_history
biar gak ada caption yang kebetulan sama persis kayak sebelumnya.

Dua mode:
- generic       : caption promo generik, gak nyebut nama produk (default, akun1)
- product_name  : caption dengan struktur Pain point -> Solusi (boleh sebut
                   nama produk) -> CTA promo. Dipakai kalau product_name dikasih.

Usage:
  python3 caption_generator.py <account_slug>                    # generic
  python3 caption_generator.py <account_slug> "<nama produk>"     # product_name
"""

import os
import sqlite3
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE_DIR = Path.home() / "tiktok-bot"
DB_PATH = BASE_DIR / "db" / "tiktok_bot.db"
ENV_PATH = BASE_DIR / ".env"

load_dotenv(ENV_PATH)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

GENERIC_PROMPT_TEMPLATE = """Buatkan 1 caption promosi TikTok berbahasa Indonesia yang singkat
(maksimal 2 kalimat pendek), gaya santai dan persuasif, untuk mendorong orang
memanfaatkan promo yang sedang berlangsung.

ATURAN PENTING:
- JANGAN sebut nama produk atau merek apapun (captionnya generik).
- JANGAN pakai hashtag.
- Buat kalimat yang beda dari contoh-contoh caption sebelumnya berikut ini,
  jangan menduplikasi kata-katanya:
{recent_captions}

Balas HANYA dengan teks captionnya saja, tanpa tanda kutip, tanpa penjelasan."""

PRODUCT_NAME_PROMPT_TEMPLATE = """Buatkan 1 caption promosi TikTok berbahasa Indonesia untuk produk/buku
berjudul "{product_name}".

STRUKTUR WAJIB (gabung jadi caption yang natural mengalir, JANGAN tulis label
struktur/angka-nya di captionnya):
1. Pain point -- masalah/kegelisahan yang relevan dengan tema dari judul ini
2. Solusi -- arahkan ke produk ini sebagai jawabannya, boleh sebut judulnya
3. CTA -- ajak orang manfaatkan promo sekarang

ATURAN PENTING:
- Maksimal 3 kalimat pendek.
- JANGAN pakai hashtag.
- JANGAN mengarang klaim spesifik yang gak bisa dipastikan dari judul doang
  (misal: statistik, kutipan persis dari isi, nama tokoh spesifik di dalamnya).
  Cukup pakai kesan umum dari judulnya aja.
- Buat kalimat yang beda dari contoh-contoh caption sebelumnya berikut ini,
  jangan menduplikasi kata-katanya:
{recent_captions}

Balas HANYA dengan teks captionnya saja, tanpa tanda kutip, tanpa penjelasan."""


def get_recent_captions(cur, account_id, limit=10):
    cur.execute(
        "SELECT caption_text FROM captions_history WHERE account_id=? ORDER BY id DESC LIMIT ?",
        (account_id, limit),
    )
    return [r[0] for r in cur.fetchall()]


def call_gemini(prompt):
    from google import genai
    client = genai.Client(api_key=GEMINI_API_KEY)
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
    )
    text = (response.text or "").strip()
    if not text:
        raise RuntimeError("Gemini balikin response kosong")
    return text


def call_deepseek(prompt):
    resp = requests.post(
        "https://api.deepseek.com/chat/completions",
        headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
        json={
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 1.0,
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    text = data["choices"][0]["message"]["content"].strip()
    if not text:
        raise RuntimeError("DeepSeek balikin response kosong")
    return text


def generate_unique_caption(cur, account_id, product_name=None):
    """product_name=None -> caption generik. product_name diisi -> caption
    dengan struktur Pain>Solusi(sebut nama)>CTA."""
    recent = get_recent_captions(cur, account_id)
    recent_str = "\n".join(f"- {c}" for c in recent) if recent else "(belum ada caption sebelumnya)"

    if product_name:
        prompt = PRODUCT_NAME_PROMPT_TEMPLATE.format(product_name=product_name, recent_captions=recent_str)
    else:
        prompt = GENERIC_PROMPT_TEMPLATE.format(recent_captions=recent_str)

    try:
        print("Coba generate pakai Gemini...")
        caption = call_gemini(prompt)
    except Exception as e:
        print(f"Gemini gagal: {e}")
        print("Fallback ke DeepSeek...")
        try:
            caption = call_deepseek(prompt)
        except Exception as e2:
            print(f"DeepSeek juga gagal: {e2}")
            print("STOP -- kedua provider gagal, gak retry lagi.")
            return None

    if caption in recent:
        print("WARNING: caption sama persis kayak history, tapi tetep dipakai"
              " (retry policy kita cuma 1x alternatif, bukan loop).")

    return caption


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 caption_generator.py <account_slug> [\"<nama produk>\"]")
        sys.exit(1)

    account_slug = sys.argv[1]
    product_name = sys.argv[2] if len(sys.argv) > 2 else None

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("SELECT id FROM accounts WHERE slug=?", (account_slug,))
    row = cur.fetchone()
    if not row:
        print(f"GAK KETEMU akun '{account_slug}'.")
        sys.exit(1)
    account_id = row[0]

    caption = generate_unique_caption(cur, account_id, product_name)
    if caption is None:
        conn.close()
        sys.exit(1)

    print(f"\nCaption hasil: \"{caption}\"")

    cur.execute(
        "INSERT INTO captions_history (account_id, caption_text) VALUES (?, ?)",
        (account_id, caption),
    )
    conn.commit()
    conn.close()
    print("Tersimpan ke captions_history.")


if __name__ == "__main__":
    main()
