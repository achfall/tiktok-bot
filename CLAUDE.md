# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A bot that posts affiliate product videos to multiple TikTok accounts (`akun1`, `akun2`, …) by driving real Chrome instances over the Chrome DevTools Protocol (raw `websocket` + `requests`, no Selenium/Playwright). Each post uploads a video in TikTok Studio, pastes an AI-generated caption, attaches a product link found by TikTok product ID, and renames the link to "Promo Tiktok Disini". Code comments, logs and docs are in Indonesian. `HERMES_INSTRUCTIONS.md` is the operator runbook (written for another agent) and is the authoritative reference for day-to-day operations.

## Environment

- Python venv at `venv/` (`source venv/bin/activate`). Deps: `requests`, `websocket-client`, `python-dotenv`. No requirements file, no test suite, no linter.
- `.env` holds `GEMINI_API_KEY` and `DEEPSEEK_API_KEY` (caption generation).
- All paths are hardcoded to `~/tiktok-bot/...` (`Path.home() / "tiktok-bot"`); some older scripts hardcode `/home/sintia/tiktok-bot` and `CDP_PORT = 9221`.
- State lives in SQLite: `db/tiktok_bot.db`.

## Running

Production runs are **systemd user services**, not tmux (`scripts/boot_start_all.sh` is the obsolete tmux/cron launcher — don't use it, it would start duplicate orchestrators):

```bash
systemctl --user status tiktokbot-akun1 tiktokbot-akun2 tiktokbot-akun3 --no-pager
systemctl --user restart tiktokbot-akun1          # e.g. after patching daily_orchestrator.py
tail -f logs/akun1-service.log
```

Unit files: `~/.config/systemd/user/tiktokbot-akunN.{service,timer}`. The service runs `start-chrome-profile.sh akunN <port>` then `daily_orchestrator.py akunN --publish`; the timer restarts it daily at 10:00. CDP port convention: `9220 + N` (akun1=9221 … akun4=9224). A new account needs its own service + timer copied from the existing pattern.

Manual / testing (these hit the real TikTok account — only `none` mode is side-effect free):

```bash
scripts/start-chrome-profile.sh akun1 9221                  # launch Chrome for a profile with CDP
python3 scripts/daily_orchestrator.py akun1                  # smoke test: 1 product, stops at READY_TO_POST
python3 scripts/daily_orchestrator.py akun1 --publish --limit 5
python3 scripts/caption_generator.py akun2 "Nama Produk"     # try caption generation alone
python3 scripts/cleanup_blank_tabs.py 9221                   # close leftover blank tabs
```

Product management must go through the CLI (CSV in `config/` is legacy, no longer the source of truth; don't edit the DB by hand for this):

```bash
python3 scripts/manage_products.py list|add|disable|enable|set-id akun1 "<folder_name>" [product_id]
```

`folder_name` must exactly match the folder under `content/<account>/`; `add` requires the videos to already be in that folder because it scans them into `contents`. `scripts/db/scan_contents.py <account>` rescans folders for new videos.

## Architecture

**Data model** (`scripts/db/schema.sql`): `accounts` (slug, `cdp_port`, `publish_mode`, `caption_style`) → `products` (one per folder in `content/<slug>/`, ordered by `sort_order`, with `tiktok_product_id`, `active`) → `contents` (one row per video file, `status` unused/used). `posts_log` records every attempt (`success` / `draft` / `failed`); `captions_history` is used to avoid repeating captions. Note: `accounts.caption_style` exists in the live DB (added via ALTER) but is missing from `schema.sql`.

**`scripts/daily_orchestrator.py`** is the production core:
- Picks active products not yet posted today (per `posts_log`), in `sort_order`, capped at `DEFAULT_MAX_PER_DAY`; for each, takes the first `unused` video.
- Mode comes from the DB, not only the CLI: no `--publish` → `none` (test one product); `publish_mode='auto'` → clicks **Post**; anything else (`manual_review`) → clicks **Save draft**. Videos are marked used once posted *or* drafted.
- Waits for the posting window (from 10:00) and sleeps a random 35–50 min between posts.
- Failure policy: each UI step retries once (`find_with_retry`); any `StepFailed` or caption-generation failure is logged and **stops the whole day** (no skip-and-continue). The systemd timer is what resumes it the next day.
- `post_one_video` opens a fresh tab via `/json/new`, locates elements with injected JS returning bounding-box centers, then clicks/types with `Input.dispatchMouseEvent` / `Input.dispatchKeyEvent` / `Input.insertText`. Caption is pasted via clipboard. Step 5b handles accounts whose link popup has a "My Shop / Showcase products" toggle. Success is detected by the post-action redirect URL (`tiktokstudio/content` or `tab=draft`).

**`scripts/caption_generator.py`**: Gemini first, DeepSeek fallback, called via plain HTTP. `caption_style='generic'` produces brand-free promo captions; `'product_name'` produces pain-point → solution → CTA captions using the folder name. Recent captions are fed into the prompt and checked against `captions_history` for uniqueness.

**Selector knowledge** was discovered step by step in `scripts/mapping/step*.py` (standalone exploratory scripts, hardcoded to port 9221). `full_test_post.py` and `test_save_draft.py` are older standalone end-to-end tests that duplicate the pipeline logic. When TikTok's UI changes, the fix belongs in `daily_orchestrator.py`; the other scripts will not stay in sync.

`chrome-profiles/akunN/` are logged-in Chrome user-data dirs — never delete or reset them (that logs the account out).
