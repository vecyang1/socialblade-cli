# System Architecture — `socialblade-cli` (`socialblade`)

> Scope: project-local — system architecture and module/data/integration map for `socialblade`.

## App Summary

`socialblade` (`socialblade-cli`) is a Single Source of Truth (SSOT) CLI and Python library for creator historical growth analytics, daily/monthly subscriber & view deltas, Social Blade grades/rankings, and cross-platform competitor comparison across **YouTube, TikTok, Instagram, Twitch, Facebook, and Twitter/X**.

It directly solves three structural gaps in existing open-source Social Blade wrappers:
1. **Cloudflare 403 Challenge & Ad-Auction Page Hangs**: Pure HTTP scrapers (`python-socialblade`) fail 100% with HTTP 403 (`cf-mitigated: challenge`), while full browser page navigations take 15–25s due to ad-bidding scripts and only render 14 days in the DOM table. `socialblade` supports 3 pluggable browser backends (`ego`, `cdp`, `playwright`), anchors a tab on `https://socialblade.com/robots.txt` (zero ads), and queries Social Blade's Next.js `__NEXT_DATA__` + `/api/trpc/{platform}.history` (`limit=30`) + `{platform}.growth` + `youtube.monthly` (30m) in ~2–6s.
2. **Twitter / X Discontinuation on Social Blade**: Social Blade completely shut down Twitter/X support (`HTTP 404`). `socialblade` bridges `twitter` / `x.com` targets to direct `TIKHUB_API_KEY` REST calls or `tikhub-twitter` + the local SQLite SSOT snapshot ledger (`snapshots.db`).
3. **Canonical Entity Deduplication & Derived Deltas**: Stores point-in-time daily snapshots keyed by immutable `canonical_id` (`<platform>:<immutable_platform_id>`) and derives all daily/window deltas dynamically on read after Read-After-Write verification.

## System Diagram

```text
[CLI / Agent Input] (URL, @handle, platform:query)
       │
       ▼
[router.parse_target() + router.select_tier_plan()] (Rules R1–R11)
       │
       ├──► Tier 1: [Matrix API] (matrix.sbapis.com/b/{platform}/statistics)
       ├──► Tier 2: [Browser tRPC] (ego | cdp | playwright -> /robots.txt anchor -> __NEXT_DATA__ + /api/trpc/*)
       └──► Tier 3: [Platform Bridge] (TikHub API/CLI for Twitter/X, yt-data for YouTube fallback)
       │
       ▼
[ledger.upsert_report()] ──► Write point-in-time rows to SQLite SSOT (~/.local/share/socialblade-cli/snapshots.db)
       │
       ▼
[Read-After-Write Re-Query] (get_daily_snapshots with limit+1 boundary row)
       │
       ▼
[derive_daily_deltas() + CompletenessReceipt] ──► CLI Table / JSON Output
```

## Module Map

| Module | Responsibility |
| --- | --- |
| `bin/socialblade` | CLI executable entrypoint (`socialblade`). |
| `src/socialblade_core/config.py` | Zero-hardcoding configuration resolver (`cli > env > default`) with source provenance and secret redaction. |
| `src/socialblade_core/types.py` | Schema-first dataclasses (`ParsedTarget`, `DailyPoint`, `MonthlyPoint`, `GrowthSummary`, `CompletenessReceipt`, `CreatorStatsReport`). |
| `src/socialblade_core/browser.py` | Multi-backend browser execution engine (`ego`, `cdp`, `playwright`) and single source of truth for in-page JS (`STATS_FN_JS`, `FAVORITES_FN_JS`). |
| `src/socialblade_core/router.py` | Deterministic URL/handle parser (`R1`–`R11` rules) and tier execution planner (`select_tier_plan`) powering `socialblade explain`. |
| `src/socialblade_core/ledger.py` | SQLite SSOT (`snapshots.db`) managing `creators`, `creator_aliases`, and `daily_snapshots` with Read-After-Write verification and dynamic delta calculation (`derive_daily_deltas`). |
| `src/socialblade_core/providers.py` | 3-Tier provider engine (`fetch_matrix_api`, `fetch_ego_trpc`, `fetch_ego_favorites`, `fetch_platform_bridge`, `execute_stats_flow`). |
| `src/socialblade_core/cli.py` | CLI subcommand router (`stats`, `compare`, `favorites`, `ledger`, `explain`, `diagnose`). |
| `tests/test_socialblade_cli.py` | Unit and two-sided verification test suite. |

## Data And Storage

- **SQLite SSOT (`~/.local/share/socialblade-cli/snapshots.db`)**:
  - `creators`: `canonical_id TEXT PRIMARY KEY` (`<platform>:<immutable_id>`), `platform`, `platform_id`, `username`, `display_name`, `handle`, `grade`, `sb_rank`, `follower_rank`, `views_rank`, `country`, `category`, `created_at`, `last_source`, `updated_at`.
  - `creator_aliases`: `alias_key TEXT PRIMARY KEY` (`<platform>:<lower_alias>`), `canonical_id TEXT NOT NULL`.
  - `daily_snapshots`: `(canonical_id, snapshot_date)` primary key with raw point-in-time counters (`followers`, `following`, `views`, `posts`, `likes`, `engagement_rate`, `source`, `fetched_at`). **Zero static delta columns** are stored.

## Integrations And External Services

- **Social Blade Business Matrix API (`https://matrix.sbapis.com/b/{platform}/statistics`)**: Tier 1 authenticated JSON endpoint (and free test sample `query=socialblade`).
- **Social Blade Next.js SSR + tRPC API (`https://socialblade.com/api/trpc/*`)**: Tier 2 extraction via `playwright`, `cdp`, or `ego-browser` anchored on `/robots.txt`.
- **TikHub Twitter Bridge (`TIKHUB_API_KEY` or `tikhub-twitter`)**: Tier 3 canonical bridge for Twitter/X profiles discontinued by Social Blade.
- **YouTube Data CLI Bridge (`yt-data`)**: Tier 3 fallback for YouTube channel metadata when browser backends are offline.

## Runtime And Deployment

- **Installation**: `pip install -e .` or `pip install -e ".[browser]"` (exposes `socialblade` on `PATH`).
- **Configuration Precedence**: `CLI Flag > Environment Variable > Default`.
- **Health & Diagnostic Commands**: `socialblade diagnose [--json]` and `socialblade explain <target> [--json]`.
