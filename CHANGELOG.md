# Changelog

All notable changes to `socialblade-cli` (`socialblade`) are documented in this file.

## [1.2.1] - 2026-10-24

### Governance & Operational Alignment
- **Cadence & Sidecar Audit Policy (`SKILL.md`, `AGENTS.md`)**:
  - Evaluated against `scheduled-task-rescheduler § Anti-Fragmentation & SaaS Tracking Invariant` and `wheel-check § Reuse Ladder`.
  - Formally codified the **Zero-Isolated-Sidecar Invariant**: skipped creating standalone single-SaaS background daemons for Social Blade alone to prevent snippet rot and ghost logic. Reusable background polling is achieved via native `socialblade watch` or integration into cross-platform growth cadences (`CAD-20261023-portfolio-traffic-signal-journey-report` / `daily-intel-radar`).
- **Owner Surface Triad & Choosing the Rung**:
  - Hardened Twitter discontinuation and Business API 401 handling across Rung 1 (Code Tier 3 bridge) and Rung 2 (`SKILL.md § Gotchas & API Boundaries`).
  - Registered canonical tool memory pointer in central tool memory hub (`05 - Memory Center/tools/socialblade-cli.md`).

## [1.2.0] - 2026-10-24

### Added
- **Creator Post Engagement Stream (`socialblade posts`)**:
  - Bridges YouTube (`yt-data channel` stream) and Twitter/X (`tikhub` tweets API/CLI) to fetch recent videos/shorts/tweets with views, likes, comments, shares, and bookmarks.
  - Persists raw post observations in SQLite `creator_posts` (SSOT) and dynamically derives `engagement_rate = (likes + comments + shares) / views * 100` on read after Read-After-Write verification.
- **Unified Growth + Post Engagement Timeline (`socialblade timeline`)**:
  - Joins chronological daily follower/view deltas from `daily_snapshots` with published posts from `creator_posts` by date (`YYYY-MM-DD`), surfacing top-performing posts and highest-growth days.
- **5-Year Future Growth Projections (`socialblade projections`)**:
  - Extracts 1,826-day Holt's Linear forecasts from Social Blade tRPC (`/api/trpc/{platform}.projection`) or velocity linear projections, plus automated round milestone crossings (`MilestoneCrossing`).
- **Creator Discovery Search & Watch Loop (`socialblade search`, `socialblade watch`)**:
  - Added multi-platform creator discovery (`{platform}.search` tRPC + TikHub bridge) and scheduled snapshot/post polling (`watch`) for cron/launchd cadences.

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
