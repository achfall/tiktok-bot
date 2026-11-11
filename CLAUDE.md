# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A bot that posts affiliate product videos to multiple TikTok accounts (`akun1`, `akun2`, …) by driving real Chrome instances over the Chrome DevTools Protocol (raw `websocket` + `requests`, no Selenium/Playwright). Each post uploads a video in TikTok Studio, pastes an AI-generated caption, attaches a product link found by TikTok product ID, and renames the link using the account setting `product_link_name` (default defined in `scripts/bot_settings.py`). Code comments, logs and docs are in Indonesian. `HERMES_INSTRUCTIONS.md` is the operator runbook (written for another agent) and is the authoritative reference for day-to-day operations.

## Environment

- Python venv at `venv/` (`source venv/bin/activate`). Deps: `requests`, `websocket-client`, `python-dotenv`. Dependencies are listed in requirements.txt; archive tests are in tests/ (unittest); no linter.
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

Manual commands below access real Chrome/TikTok or AI providers. Orchestrator `none` also uploads a real video; it only skips the final submission. Use isolated unit tests for development:

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

**Data model** (`scripts/db/schema.sql`): `accounts` (slug, `cdp_port`, `publish_mode`, `caption_style`) → `products` (one per folder in `content/<slug>/`, ordered by `sort_order`, with `tiktok_product_id`, `active`) → `contents` (one row per video file, `status` unused/used). `posts_log` records every attempt (`success` / `draft` / `failed`); `captions_history` is used to avoid repeating captions. `accounts.caption_style` is included in schema.sql for new installations; older databases may need a migration.

**`scripts/daily_orchestrator.py`** is the production core:
- Picks active products not yet posted today (per `posts_log`), in `sort_order`, capped at `DEFAULT_MAX_PER_DAY`; for each, takes the first `unused` video.
- Mode comes from the DB, not only the CLI: no `--publish` → `none` (test one product); `publish_mode='auto'` → clicks **Post**; `manual_review` → **Save draft**; `schedule` → native **Schedule**. Unknown modes and inactive accounts are rejected. Videos are marked used once posted *or* drafted.
- Waits for the posting window (from 10:00) and sleeps a random 35–50 min between posts.
- Failure policy: each UI step retries once (`find_with_retry`); any `StepFailed` or caption-generation failure is logged and **stops the whole day** (no skip-and-continue). The systemd timer is what resumes it the next day.
- `post_one_video` opens a fresh tab via `/json/new`, locates elements with injected JS returning bounding-box centers, then clicks/types with `Input.dispatchMouseEvent` / `Input.dispatchKeyEvent` / `Input.insertText`. Caption is inserted with Input.insertText without clipboard permission. Step 5b handles accounts whose link popup has a "My Shop / Showcase products" toggle. Publish/draft success uses the post-action redirect; Schedule also requires a matching caption, schedule marker, date and time in Posts.

**`scripts/caption_generator.py`**: Gemini first, DeepSeek fallback, called via plain HTTP. `caption_style='generic'` produces brand-free promo captions; `'product_name'` produces pain-point → solution → CTA captions using the folder name. Recent captions are fed into the prompt and checked against `captions_history` for uniqueness.

**Selector knowledge** was discovered step by step in `scripts/mapping/step*.py` (standalone exploratory scripts, hardcoded to port 9221). `full_test_post.py` and `test_save_draft.py` are archived reference code; their entrypoints are disabled because they bypass current queue and quota rules. When TikTok's UI changes, the fix belongs in `daily_orchestrator.py`; the other scripts will not stay in sync.

`chrome-profiles/akunN/` are logged-in Chrome user-data dirs — never delete or reset them (that logs the account out).

## macOS local setup

See README.md for the macOS launcher, read-only check, migration and inert
launchd generator. macOS uses Command shortcuts; Linux retains Ctrl/systemd.
Do not run the upload pipeline to validate setup. launchd must remain unloaded
and its posting guard absent until the operator explicitly authorizes activation.

After verified success/draft, archive_used.py moves used videos to the product
sudah-dipakai/ directory and updates DB paths. The scanner does not recurse into
archives. Operators may delete archived files manually; retain used/history rows.

## Shared concepts (current queue)

README.md is the current setup reference. `content_groups` maps concepts to
content roots; accounts.content_group_id selects the source. Products/IDs remain
per account. `contents.source_folder` identifies one shared queue, independently
of the historical product_id owner. Never select a shared video by product_id
alone. content_queue.py scans, atomically claims with BEGIN IMMEDIATE and a token,
records submitting before the final button, completes used/success or draft,
and holds uncertain results for manual reconciliation. No automatic lease expiry.
Scanner runs at orchestrator startup. Archive path for concepts is
content/<concept>/<product>/sudah-dipakai/<Chrome-profile-name>/.
Use manage_concepts.py and manage_queue.py for setup/recovery, not manual SQL.
Retest creates a separate file/row; --test-draft forces draft without changing DB.
Local tests: venv/bin/python -m unittest discover -s tests -v. CDP/API calls are
simulated; a real TikTok test must be run from the user's local Terminal.

## Local web panel

Read AGENTS.md and DEVELOPMENT.md for current architecture. The buildless local
panel uses botapp/service.py as a transport-independent application adapter to
existing CLIs/domain code, botapp/server.py for HTTP/scheduling and web/ for UI.
Configuration definitions live in scripts/bot_settings.py and are read by both
UI and CLI orchestrator. Data can be isolated with TIKTOK_BOT_HOME. Job metadata
is separate from the bot DB. Do not duplicate queue/posting rules in web code.
Keep UI/API/schema/tests synchronized when changing features. No real upload or
publish tests are triggered by the test suite or by simply starting the panel.
README.md is the current operator runbook. HERMES_INSTRUCTIONS.md is no longer
present in this checkout; older references above are historical.

## Personal flat account sources (current default)

Read README.md for the operator workflow. New accounts without --concept use
content/<Chrome-profile-name>/ with ID-produk.txt (one numeric ID per line),
MP4/MOV directly in the root, and sudah-dipakai/ for verified used videos.
account_sources.py owns ID validation, product synchronization and persistent
round-robin assignment. account_sources stores each private root/cursor;
content_assignments stores an immutable video/product-ID pair before upload.
Do not change a failed video's pair or increment the cursor on retry. Daily
limits in flat mode count videos, not distinct products. Old concept accounts
remain supported. Use migrate_account_source.py for backed-up explicit migration;
never manually reset status/history or rename the Chrome profile as part of it.
