---
name: socialblade-cli
description: "Unified Social Blade & Cross-Platform Creator Growth CLI (`socialblade`): combines Official Matrix Business API (Tier 1), zero-cost ego-browser Next.js/tRPC 30-day & 30-month extraction (Tier 2), TikHub Twitter/X & yt-data bridges for capabilities Social Blade lacks (Tier 3), diagnostic explain/diagnose commands, and a local SQLite SSOT snapshot ledger."
---

# Social Blade & Cross-Platform Creator Growth CLI (`socialblade`)

Unified creator growth analytics, historical daily/monthly time-series extraction, cross-platform comparison, and local SQLite snapshot ledger (`snapshots.db`) across YouTube, TikTok, Instagram, Twitch, Facebook, and Twitter/X.

## Architectural Overview (3-Tier Provider Engine + SQLite SSOT)

1. **Tier 1 (`matrix_api` — Official Social Blade Business API)**:
   - Endpoint: `https://matrix.sbapis.com/b/{platform}/statistics`
   - Activated automatically when `SOCIALBLADE_CLIENT_ID` + `SOCIALBLADE_TOKEN` are configured (via CLI flags, environment variables, or 1Password) OR when querying the official free sample (`query=socialblade` on `youtube`, `instagram`, `twitch`, `facebook`).
   - Sub-300ms direct JSON response without browser overhead.
2. **Tier 2 (`ego_trpc` — Zero-Cost Multi-Backend Next.js/tRPC Engine)**:
   - Supports 3 pluggable browser backends (`--browser-backend auto|ego|cdp|playwright`): local `ego-browser`, attach to any running Chrome via CDP (`SOCIALBLADE_CDP_URL`), or launch standalone Playwright + system Chrome (`headless=False` to pass Cloudflare Turnstile).
   - Anchors a lightweight tab on `https://socialblade.com/robots.txt` (zero ad-auction scripts), fetches SSR `__NEXT_DATA__`, and batches `/api/trpc/{platform}.history` (`limit=30`) + `{platform}.growth` + `youtube.monthly` (30 months) inside `page.evaluate()`, extracting full 30-day daily tables, 1d–365d/YTD growth deltas, grades, and rankings for **free** across YouTube, TikTok, Instagram, Twitch, and Facebook.
3. **Tier 3 (`platform_bridge` — Bridging What Social Blade Lacks)**:
   - **Twitter / X Discontinuation Bridge**: Social Blade discontinued Twitter/X tracking globally (`HTTP 404` on both Matrix API and Web). Whenever a Twitter/X handle or `x.com`/`twitter.com` URL is passed, `socialblade` automatically bridges to `TIKHUB_API_KEY` (direct TikHub REST API) or `tikhub-twitter` + the local SQLite SSOT snapshot ledger (`snapshots.db`) to record and compute historical follower/tweet deltas (`twitter:<rest_id>`).
   - **YouTube Zero-Browser Fallback**: If `ego-browser` is unavailable and no Matrix API key is configured, falls back to `yt-data` (`youtube_data_core`) + the local SQLite snapshot ledger.
4. **Local SQLite SSOT Ledger (`~/.local/share/socialblade-cli/snapshots.db`)**:
   - **Stable Canonical IDs**: Keyed by `<platform>:<immutable_platform_id>` (e.g., `youtube:UC8kOCg0Cf8Kw7b_YikLlAig`, `twitter:1825804961164451840`, `tiktok:127905465618821121`), never by mutable handles or translated display names. `creator_aliases` maps handles/usernames to `canonical_id`.
   - **Derived Deltas Only ("能派生的不要存")**: `daily_snapshots` stores only point-in-time raw observations; daily diffs (`delta_followers`, `delta_views`, `delta_posts`) and window deltas (`1d`, `7d`, `14d`, `30d`, daily/weekly averages) are derived dynamically on read after **Read-After-Write verification**.
   - **Anti-Truncation Receipt (`CompletenessReceipt`)**: Explicitly verifies `returned_days` vs `requested_days` and emits `is_complete=False` with a human-readable `truncation_warning` whenever a window is partial.

## CLI Usage

```bash
# 1. Subsystem health & configuration provenance diagnosis
socialblade diagnose
socialblade diagnose --json

# 2. Diagnostic target parser & routing rule explanation (calls real router & ledger)
socialblade explain "https://www.youtube.com/@vecyang/videos"
socialblade explain "https://x.com/HTX_Studio"
socialblade explain tiktok:khaby.lame --json

# 3. Creator growth stats (30-day daily table + multi-window growth + grades/ranks)
socialblade stats youtube @vecyang --monthly
socialblade stats "https://www.youtube.com/@vecyang/videos" --json
socialblade stats youtube socialblade
socialblade stats tiktok @khaby.lame
socialblade stats instagram @instagram
socialblade stats "https://x.com/HTX_Studio"

# 4. Side-by-side cross-platform comparison
socialblade compare youtube:@vecyang twitter:@HTX_Studio youtube:socialblade

# 5. Authenticated Social Blade Favorites
socialblade favorites --platform youtube

# 6. Inspect local SQLite SSOT snapshot ledger & historical time series
socialblade ledger list
socialblade ledger history youtube:@vecyang --days 30
socialblade ledger history twitter:@HTX_Studio --json

# 7. Historical post engagement stream & unified daily delta + post timeline
socialblade posts youtube @vecyang --limit 5
socialblade posts twitter @HTX_Studio --limit 5
socialblade timeline youtube @vecyang --days 14
socialblade timeline twitter @HTX_Studio --days 14 --post-limit 5

# 8. 5-year Holt's Linear future projections, creator search & watch loop
socialblade projections youtube @vecyang
socialblade search vecyang --platform youtube
socialblade watch youtube:@vecyang --iterations 1 --tier bridge
```

## Gotchas & API Boundaries

Applying `skill-creator § Choosing the Rung`:

1. **Twitter / X Discontinuation (Rung 1 Code + Rung 2 Skill)**:
   - *Condition*: Social Blade permanently discontinued Twitter/X tracking (`HTTP 404` on both Matrix API and Web).
   - *Behavior*: Attempting direct scraping yields 404 errors. `socialblade` automatically catches Twitter targets and delegates to Tier 3 (`tikhub-twitter` / `TIKHUB_API_KEY`) plus the local SQLite SSOT ledger.
2. **Post-Level Endpoints Require Paid Business API (Rung 1 Code + Rung 2 Skill)**:
   - *Condition*: Social Blade Matrix API `videos` endpoint returns `HTTP 401 Unauthorized` for free or unauthenticated tiers.
   - *Behavior*: `socialblade posts` automatically routes to native platform bridges (`yt-data` for YouTube, TikHub for Twitter/X) to retrieve live video/tweet metrics without paid API locks.
3. **Cloudflare Turnstile & Ad-Auction Overhead (Rung 1 Code)**:
   - *Condition*: Navigating directly to `socialblade.com/youtube/user/*` triggers aggressive Cloudflare verification and heavy ad-auction scripts.
   - *Behavior*: Tier 2 anchors on `https://socialblade.com/robots.txt` (zero ad overhead) and evaluates Next.js `/api/trpc/*` endpoints directly inside `page.evaluate()`.

## Cadence & Scheduled Automation Policy

Applying `scheduled-task-rescheduler § Anti-Fragmentation & SaaS Tracking Invariant` and `wheel-check § Reuse Ladder`:

- **Zero-Isolated-Sidecar Invariant**: **SKIP** creating dedicated, standalone cron/launchd daemons for Social Blade alone. Isolated single-SaaS scrapers lead to ghost logic, token waste, and unmonitored failures.
- **Native Polling via CLI**: For developer/operator monitoring loops, use the built-in `socialblade watch <target> --interval <seconds> --iterations <N>` command.
- **Portfolio-Level Integration**: When recurring creator growth tracking is needed across accounts, integrate `socialblade` into existing multi-platform cadences (such as `CAD-20261023-portfolio-traffic-signal-journey-report` or `daily-intel-radar`) rather than creating isolated sidecars.
- **SSOT Ledger Hygiene**: Point-in-time observations are safely committed to `~/.local/share/socialblade-cli/snapshots.db`, with daily/weekly deltas derived on read after Read-After-Write verification.

## Verification Commands

```bash
# Run unit & two-sided verification suite
python3 -m unittest discover -s tests -v

# Verify global PATH availability from $HOME
command -v socialblade
socialblade diagnose
```

## Related Skills

- [scheduled-task-rescheduler](../scheduled-task-rescheduler/SKILL.md): Owns cadence lifecycle, anti-fragmentation invariants, and scheduled task audits.
- [wheel-check](../wheel-check/SKILL.md): Owns the reuse ladder and anti-duplication protocols.
- [skill-creator](../skill-creator/SKILL.md): Owns Choosing the Rung and the Owner Surface Triad.
- [tikhub-social-data](../tikhub-social-data/SKILL.md): Owns `tikhub-twitter` used by Tier 3 Twitter/X bridge.
- [youtube-data-cli](../youtube-data-cli/SKILL.md): Owns `yt-data` used by Tier 3 YouTube fallback and video-level inspection.
- [ego-browser](../ego-browser/SKILL.md): Owns the local Chromium session used by Tier 2 `ego_trpc` extraction.
