# Changelog

All notable changes to `socialblade-cli` (`socialblade`) are documented in this file.

## [1.1.0] - 2026-10-24

### Added
- **Multi-Backend Browser Engine (`src/socialblade_core/browser.py`)**:
  - Added pluggable browser backends (`--browser-backend auto|playwright|cdp|ego`) so open-source users can extract 30-day & 30-month Social Blade Next.js/tRPC time-series using standalone Playwright + system Chrome (`pip install "socialblade-cli[browser]"`), attach to an existing Chrome session via CDP (`--cdp-url`), or use local `ego-browser`.
  - Consolidated in-page JS (`STATS_FN_JS`, `FAVORITES_FN_JS`) into a single source of truth shared across all browser backends.
- **Direct TikHub REST API Bridge for Twitter/X (`src/socialblade_core/providers.py`)**:
  - Added native `TIKHUB_API_KEY` HTTP client in Tier 3 (`_fetch_tikhub_twitter_http`) to bridge Twitter/X creator profiles (discontinued by Social Blade) without requiring external CLI wrappers.
- **Open-Source Packaging**:
  - Added `pyproject.toml`, MIT `LICENSE`, `.env.example`, and `--tier web` alias.

## [1.0.0] - 2026-10-23

### Added
- **3-Tier Provider Engine (`src/socialblade_core/providers.py`)**:
  - **Tier 1 (`matrix_api`)**: Official Social Blade Business API (`https://matrix.sbapis.com/b/{platform}/statistics`) supporting authenticated `clientid` + `token` headers and zero-key official sample queries (`query=socialblade`).
  - **Tier 2 (`ego_trpc`)**: Zero-cost, Cloudflare-proof extraction anchored on `https://socialblade.com/robots.txt` (zero ad-auction overhead), parsing Next.js `__NEXT_DATA__` and batching `/api/trpc/{platform}.history` (30-day expansion over the 15-day SSR table), `{platform}.growth`, and `youtube.monthly` (30-month series) across YouTube, TikTok, Instagram, Twitch, and Facebook.
  - **Tier 3 (`platform_bridge`)**: Bridges capabilities that Social Blade lacks — automatically routes Twitter/X queries (discontinued by Social Blade) to TikHub + local SQLite ledger, and provides a `yt-data` zero-browser fallback for YouTube.
- **SQLite SSOT Snapshot Ledger (`src/socialblade_core/ledger.py`)**:
  - Keyed by stable `canonical_id` (`<platform>:<immutable_platform_id>`) with `creator_aliases` mapping mutable handles/usernames to the single canonical entity.
  - Enforces the derived-delta invariant: stores only point-in-time observations in `daily_snapshots` and computes daily/window deltas dynamically on read after Read-After-Write verification.
  - Emits `CompletenessReceipt` (`is_complete`, `returned_days`, `requested_days`, `truncation_warning`) to prevent silent window truncation.
- **Diagnostic-First CLI Surface (`bin/socialblade`)**:
  - Added `stats`, `compare`, `favorites`, `ledger`, `explain`, and `diagnose` subcommands.
