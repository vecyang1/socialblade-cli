# socialblade-cli (`socialblade`)

> **Unified Social Blade & Cross-Platform Creator Growth CLI** — Combines Official Social Blade Business Matrix API (Tier 1), zero-cost multi-backend Next.js + tRPC 30-day & 30-month extraction (Tier 2), Twitter/X & YouTube fallback bridges for capabilities Social Blade lacks (Tier 3), and a local SQLite Single Source of Truth (SSOT) time-series ledger.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](pyproject.toml)

---

## Why `socialblade-cli`?

Existing open-source Social Blade scrapers and wrappers suffer from three major limitations:

1. **Cloudflare 403 Challenge & 20-Second Ad-Auction Hangs**:
   - Legacy HTTP scrapers (`curl` / `requests` / `python-socialblade`) fail 100% of the time with `HTTP 403 Forbidden (cf-mitigated: challenge)`.
   - Naive browser scrapers that navigate directly to full Social Blade profile pages wait 15–25 seconds on heavy ad-bidding scripts and only scrape the 14-day SSR HTML table.
   - **How `socialblade-cli` solves it**: Anchors a browser context on `https://socialblade.com/robots.txt` (zero ad scripts, ~30ms load), fetches SSR `__NEXT_DATA__`, and batches Social Blade's internal Next.js tRPC endpoints (`/api/trpc/{platform}.history` with `limit=30`, `{platform}.growth`, and `{platform}.monthly` for 30 months) inside `page.evaluate()`.
2. **Twitter / X Discontinuation on Social Blade**:
   - Social Blade discontinued Twitter/X tracking globally (`HTTP 404` on both Matrix API and Web).
   - **How `socialblade-cli` solves it**: Automatically bridges `twitter` / `x.com` targets to TikHub (`TIKHUB_API_KEY` or `tikhub-twitter`) and records daily observations in a local SQLite SSOT ledger (`snapshots.db`) to compute historical follower and post deltas (`twitter:<rest_id>`).
3. **SSOT Time-Series Ledger with Derived Deltas & Anti-Truncation Receipts**:
   - **Stable Canonical IDs**: Every creator is indexed by `<platform>:<immutable_platform_id>` (e.g. `youtube:UC8kOCg0Cf8Kw7b_YikLlAig`, `tiktok:127905465618821121`, `twitter:1825804961164451840`), with `creator_aliases` mapping mutable `@handles` or renamed display titles to the single canonical entity.
   - **Derived Deltas Only (Zero Redundant Columns)**: `daily_snapshots` stores only raw point-in-time counters; daily diffs (`Δ Subscribers`, `Δ Views`, `Δ Posts`) and multi-window summaries (`1d`, `7d`, `14d`, `30d`, `365d`, `YTD`) are derived dynamically on read after **Read-After-Write verification**.
   - **Anti-Truncation Receipt (`CompletenessReceipt`)**: Explicitly verifies `returned_days` vs `requested_days` and flags partial windows (`is_complete=False`) instead of silently returning truncated data.

---

## Architecture

```text
[CLI / Python Input] (URL, @handle, <platform>:<query>)
       │
       ▼
[router.parse_target() + router.select_tier_plan()] (Rules R1–R11)
       │
       ├──► Tier 1: [Matrix API] (matrix.sbapis.com/b/{platform}/statistics)
       │            Sub-300ms HTTP JSON when SOCIALBLADE_CLIENT_ID + SOCIALBLADE_TOKEN are set
       │            (or free official sample query=socialblade)
       │
       ├──► Tier 2: [Multi-Backend Browser tRPC] (playwright | cdp | ego)
       │            Anchors on /robots.txt -> parses __NEXT_DATA__ -> batches /api/trpc/*
       │            Extracts 30d daily history + 1d..365d growth + 30m monthly series for free
       │
       └──► Tier 3: [Platform Bridge] (Bridges capabilities Social Blade lacks)
                    • Twitter/X -> TIKHUB_API_KEY / tikhub-twitter + local SQLite ledger
                    • YouTube   -> yt-data zero-browser fallback + local SQLite ledger
       │
       ▼
[ledger.upsert_report()] ──► SQLite SSOT (~/.local/share/socialblade-cli/snapshots.db)
       │
       ▼
[Read-After-Write Verification + derive_daily_deltas() + CompletenessReceipt]
```

---

## Installation

```bash
# Clone the repository
git clone https://github.com/vecyang/socialblade-cli.git
cd socialblade-cli

# Install CLI with Playwright browser support
pip install -e ".[browser]"

# Verify installation
socialblade diagnose
```

---

## Quick Start & Usage

### 1. Diagnose Configuration & Subsystem Health (`diagnose`)

```bash
socialblade diagnose
socialblade diagnose --json
```

### 2. Explain Target Parsing & Routing Rules (`explain`)

Inspect how any URL or handle is recognized, which rule (`R1`–`R11`) matched, which execution tier is selected, and whether snapshots already exist in SQLite:

```bash
socialblade explain "https://www.youtube.com/@vecyang/videos"
socialblade explain "https://x.com/HTX_Studio"
socialblade explain tiktok:@khaby.lame --json
```

### 3. Fetch Creator Growth Stats & 30-Day Daily Table (`stats`)

```bash
# YouTube channel growth (30-day daily table + 30-month monthly series)
socialblade stats "https://www.youtube.com/@vecyang/videos" --monthly

# Explicit Playwright browser backend (launches local Chrome to pass Cloudflare)
socialblade stats youtube @vecyang --browser-backend playwright --days 14

# Attach to an existing Chrome instance with remote debugging enabled
socialblade stats tiktok @khaby.lame --browser-backend cdp --cdp-url http://127.0.0.1:9222

# Official Social Blade Matrix API free sample (zero credentials required)
socialblade stats youtube socialblade --tier matrix

# Twitter / X creator growth (automatically bridged to TikHub + local SQLite SSOT ledger)
socialblade stats "https://x.com/HTX_Studio"

# Structured JSON output for pipelines & AI agents
socialblade stats youtube @vecyang --json
```

### 4. Cross-Platform Side-by-Side Comparison (`compare`)

```bash
socialblade compare youtube:@vecyang twitter:@HTX_Studio youtube:socialblade
```

### 5. Local SQLite SSOT Ledger Inspection (`ledger`)

```bash
# List all tracked creators, canonical IDs, and snapshot date ranges
socialblade ledger list

# Re-compute historical daily & window deltas directly from local SQLite SSOT
socialblade ledger history youtube:@vecyang --days 30
socialblade ledger history twitter:@HTX_Studio --json
```

---

## Configuration & Environment Variables

All settings follow strict precedence: **`CLI Flag > Environment Variable > Default`** and can be inspected anytime via `socialblade diagnose`.

| Environment Variable | CLI Flag | Default | Description |
| --- | --- | --- | --- |
| `SOCIALBLADE_CLIENT_ID` | `--client-id` | `None` | Official Social Blade Business Matrix API `clientid` (Tier 1) |
| `SOCIALBLADE_TOKEN` | `--token` | `None` | Official Social Blade Business Matrix API `token` (Tier 1) |
| `SOCIALBLADE_BROWSER_BACKEND` | `--browser-backend` | `auto` | Tier 2 browser backend: `auto`, `playwright`, `cdp`, or `ego` |
| `SOCIALBLADE_CDP_URL` | `--cdp-url` | `None` | CDP endpoint (`http://127.0.0.1:9222`) when using `cdp` backend |
| `SOCIALBLADE_BROWSER_CHANNEL` | — | `chrome` | Browser channel for Playwright (`chrome`, `msedge`, `chromium`) |
| `SOCIALBLADE_BROWSER_HEADLESS` | — | `0` (`False`) | Headed Chrome (`0`) passes Cloudflare Turnstile automatically |
| `TIKHUB_API_KEY` | — | `None` | Optional TikHub API bearer token for Tier 3 Twitter/X bridge |
| `SOCIALBLADE_DB_PATH` | `--db-path` | `~/.local/share/socialblade-cli/snapshots.db` | Local SQLite SSOT snapshot database path |
| `SOCIALBLADE_DEFAULT_DAYS` | `-d` / `--days` | `30` | Default daily history window (`1`–`365`) |

---

## Repository Structure

```text
.
├── .env.example
├── .gitignore
├── CHANGELOG.md
├── LICENSE
├── README.md
├── SKILL.md
├── bin/
│   └── socialblade
├── docs/
│   ├── README.md
│   └── architecture.md
├── pyproject.toml
├── src/
│   └── socialblade_core/
│       ├── __init__.py
│       ├── browser.py
│       ├── cli.py
│       ├── config.py
│       ├── ledger.py
│       ├── providers.py
│       ├── router.py
│       └── types.py
└── tests/
    └── test_socialblade_cli.py
```

---

## Running Tests

```bash
python3 -m unittest discover -s tests -v
```

---

## License

Released under the [MIT License](LICENSE).
