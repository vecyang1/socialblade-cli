"""SQLite Single Source of Truth (SSOT) ledger for creator snapshots.

Enforces three core architectural invariants:
1. Canonical Stable IDs (`<platform>:<immutable_id>`): Creators are keyed by
   immutable platform IDs (e.g., `youtube:UC8kOCg0Cf8Kw7b_YikLlAig`,
   `twitter:1726862511535063040`), never by mutable handles or translated names.
   Mutable handles map to `canonical_id` via `creator_aliases`.
2. Derived Deltas ("能派生的不要存"): Only raw daily point-in-time observations
   are stored in `daily_snapshots`. Daily diffs (`delta_followers`, `delta_views`,
   `delta_posts`) and window deltas (1d/7d/14d/30d/averages) are derived on read.
3. Read-After-Write Verification: Every mutation re-queries the authoritative
   SQLite rows to verify persistence before returning to the caller.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .types import (
    CompletenessReceipt,
    CreatorStatsReport,
    DailyPoint,
    GrowthSummary,
    normalize_date_str,
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS creators (
    canonical_id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    platform_id TEXT NOT NULL,
    username TEXT,
    display_name TEXT,
    handle TEXT,
    grade TEXT,
    sb_rank INTEGER,
    follower_rank INTEGER,
    views_rank INTEGER,
    country TEXT,
    category TEXT,
    created_at TEXT,
    last_source TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS creator_aliases (
    alias_key TEXT PRIMARY KEY,
    canonical_id TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (canonical_id) REFERENCES creators(canonical_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS daily_snapshots (
    canonical_id TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    followers INTEGER,
    following INTEGER,
    views INTEGER,
    posts INTEGER,
    likes INTEGER,
    engagement_rate REAL,
    source TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (canonical_id, snapshot_date),
    FOREIGN KEY (canonical_id) REFERENCES creators(canonical_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_snapshots_canonical_date
    ON daily_snapshots(canonical_id, snapshot_date ASC);
"""


def derive_daily_deltas(points: list[DailyPoint]) -> list[DailyPoint]:
    """Sort daily points chronologically and compute derived daily deltas."""
    sorted_pts = sorted(points, key=lambda p: p.date)
    derived: list[DailyPoint] = []
    for idx, pt in enumerate(sorted_pts):
        prev = sorted_pts[idx - 1] if idx > 0 else None
        if prev is None:
            d_fol = pt.delta_followers
            d_views = pt.delta_views
            d_posts = pt.delta_posts
        else:
            d_fol = (
                (pt.followers - prev.followers)
                if (pt.followers is not None and prev.followers is not None)
                else None
            )
            prev_v = prev.views if prev.views is not None else prev.likes
            curr_v = pt.views if pt.views is not None else pt.likes
            d_views = (
                (curr_v - prev_v)
                if (curr_v is not None and prev_v is not None)
                else None
            )
            d_posts = (
                (pt.posts - prev.posts)
                if (pt.posts is not None and prev.posts is not None)
                else None
            )
        derived.append(
            DailyPoint(
                date=pt.date,
                followers=pt.followers,
                following=pt.following,
                views=pt.views,
                posts=pt.posts,
                likes=pt.likes,
                engagement_rate=pt.engagement_rate,
                source=pt.source,
                delta_followers=d_fol,
                delta_views=d_views,
                delta_posts=d_posts,
            )
        )
    return derived


def enrich_growth_from_daily(
    daily: list[DailyPoint],
    existing: GrowthSummary | None = None,
) -> GrowthSummary:
    """Derive missing growth summary metrics from chronological daily points."""
    g = existing or GrowthSummary()
    sorted_pts = sorted(daily, key=lambda p: p.date)

    def _metric_val(pt: DailyPoint, attr: str) -> int | None:
        if attr == "views":
            return pt.views if pt.views is not None else pt.likes
        return getattr(pt, attr, None)

    def _delta_val(pt: DailyPoint, attr: str) -> int | None:
        if attr == "followers":
            return pt.delta_followers
        if attr == "views":
            return pt.delta_views
        if attr == "posts":
            return pt.delta_posts
        return None

    def _window_diff(days_back: int, attr: str) -> int | None:
        if len(sorted_pts) >= days_back + 1:
            curr_val = _metric_val(sorted_pts[-1], attr)
            base_val = _metric_val(sorted_pts[-(days_back + 1)], attr)
            if curr_val is not None and base_val is not None:
                return int(curr_val - base_val)
            return None
        if len(sorted_pts) == days_back and days_back >= 2:
            curr_val = _metric_val(sorted_pts[-1], attr)
            first_val = _metric_val(sorted_pts[0], attr)
            first_delta = _delta_val(sorted_pts[0], attr)
            if curr_val is not None and first_val is not None:
                return int((curr_val - first_val) + (first_delta or 0))
        return None

    if len(sorted_pts) >= 2:
        if g.followers_1d is None:
            g.followers_1d = _window_diff(1, "followers")
        if g.followers_3d is None:
            g.followers_3d = _window_diff(3, "followers")
        if g.followers_7d is None:
            g.followers_7d = _window_diff(7, "followers")
        if g.followers_14d is None:
            g.followers_14d = _window_diff(14, "followers")
        if g.followers_30d is None:
            g.followers_30d = _window_diff(30, "followers")

        if g.views_1d is None:
            g.views_1d = _window_diff(1, "views")
        if g.views_3d is None:
            g.views_3d = _window_diff(3, "views")
        if g.views_7d is None:
            g.views_7d = _window_diff(7, "views")
        if g.views_14d is None:
            g.views_14d = _window_diff(14, "views")
        if g.views_30d is None:
            g.views_30d = _window_diff(30, "views")

        if g.posts_30d is None:
            g.posts_30d = _window_diff(30, "posts")

    if g.followers_30d is not None:
        g.daily_avg_followers = round(g.followers_30d / 30.0, 2)
        g.weekly_avg_followers = round((g.followers_30d / 30.0) * 7.0, 2)
    elif len(sorted_pts) >= 2:
        span = len(sorted_pts) - 1
        c_fol = _metric_val(sorted_pts[-1], "followers")
        b_fol = _metric_val(sorted_pts[0], "followers")
        if c_fol is not None and b_fol is not None:
            diff_fol = c_fol - b_fol
            g.daily_avg_followers = round(diff_fol / span, 2)
            g.weekly_avg_followers = round((diff_fol / span) * 7.0, 2)

    if g.views_30d is not None:
        g.daily_avg_views = round(g.views_30d / 30.0, 2)
        g.weekly_avg_views = round((g.views_30d / 30.0) * 7.0, 2)
    elif len(sorted_pts) >= 2:
        span = len(sorted_pts) - 1
        c_vw = _metric_val(sorted_pts[-1], "views")
        b_vw = _metric_val(sorted_pts[0], "views")
        if c_vw is not None and b_vw is not None:
            diff_vw = c_vw - b_vw
            g.daily_avg_views = round(diff_vw / span, 2)
            g.weekly_avg_views = round((diff_vw / span) * 7.0, 2)

    return g


def build_completeness_receipt(
    requested_days: int,
    returned_days: int,
    platform: str,
    data_source: str,
    pagination_verified: bool = True,
    reason_override: str | None = None,
) -> CompletenessReceipt:
    """Create an honest completeness receipt flagging any window truncation."""
    is_complete = returned_days >= requested_days
    warning: str | None = None
    if not is_complete:
        if reason_override:
            warning = reason_override
        elif platform == "twitter":
            warning = (
                f"Incomplete window: returned {returned_days}/{requested_days} daily points. "
                "Social Blade discontinued Twitter/X tracking; points are accumulated in the local SQLite ledger via tikhub-twitter."
            )
        elif returned_days == 0:
            warning = f"No daily history rows returned (0/{requested_days} days)."
        elif requested_days > 30 and "ego_trpc" in data_source:
            warning = (
                f"Incomplete window: returned {returned_days}/{requested_days} daily points. "
                "Social Blade free web tier caps daily history at ~30 days (use --monthly for 30-month series)."
            )
        else:
            warning = (
                f"Incomplete window: returned {returned_days}/{requested_days} daily points from {data_source}."
            )
    return CompletenessReceipt(
        requested_days=requested_days,
        returned_days=returned_days,
        is_complete=is_complete,
        pagination_verified=pagination_verified,
        truncation_warning=warning,
    )


class SnapshotLedger:
    """SQLite SSOT manager for cross-platform creator snapshots."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA_SQL)

    def upsert_report(
        self,
        report: CreatorStatsReport,
        *,
        requested_days: int = 30,
        extra_aliases: list[str] | None = None,
    ) -> CreatorStatsReport:
        """Persist creator metadata + daily points, then perform Read-After-Write verification."""
        now_iso = datetime.now(timezone.utc).isoformat()
        canonical_id = report.canonical_id
        if ":" not in canonical_id:
            canonical_id = f"{report.platform}:{report.platform_id}"
            report.canonical_id = canonical_id

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO creators (
                    canonical_id, platform, platform_id, username, display_name,
                    handle, grade, sb_rank, follower_rank, views_rank,
                    country, category, created_at, last_source, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(canonical_id) DO UPDATE SET
                    username = COALESCE(excluded.username, creators.username),
                    display_name = COALESCE(excluded.display_name, creators.display_name),
                    handle = COALESCE(excluded.handle, creators.handle),
                    grade = COALESCE(excluded.grade, creators.grade),
                    sb_rank = COALESCE(excluded.sb_rank, creators.sb_rank),
                    follower_rank = COALESCE(excluded.follower_rank, creators.follower_rank),
                    views_rank = COALESCE(excluded.views_rank, creators.views_rank),
                    country = COALESCE(excluded.country, creators.country),
                    category = COALESCE(excluded.category, creators.category),
                    created_at = COALESCE(excluded.created_at, creators.created_at),
                    last_source = excluded.last_source,
                    updated_at = excluded.updated_at
                """,
                (
                    canonical_id,
                    report.platform,
                    report.platform_id,
                    report.username,
                    report.display_name,
                    report.handle,
                    report.grade,
                    report.ranks.get("sb"),
                    report.ranks.get("followers") or report.ranks.get("subscribers"),
                    report.ranks.get("views"),
                    report.country,
                    report.category,
                    report.created_at,
                    report.data_source,
                    now_iso,
                ),
            )

            # Register all stable & human aliases -> canonical_id
            candidates = {
                report.platform_id,
                report.username,
                (report.handle or "").lstrip("@"),
            }
            if extra_aliases:
                for a in extra_aliases:
                    if a:
                        candidates.add(a.lstrip("@"))

            for alias in candidates:
                clean = (alias or "").strip().lower()
                if not clean:
                    continue
                alias_key = f"{report.platform}:{clean}"
                conn.execute(
                    """
                    INSERT INTO creator_aliases (alias_key, canonical_id, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(alias_key) DO UPDATE SET
                        canonical_id = excluded.canonical_id,
                        updated_at = excluded.updated_at
                    """,
                    (alias_key, canonical_id, now_iso),
                )

            # Upsert daily points (including boundary row from _untrimmed_daily, or synthesize today's point)
            points_to_write = list(report._untrimmed_daily or report.daily)
            if not points_to_write and report.totals:
                today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                points_to_write.append(
                    DailyPoint(
                        date=today_str,
                        followers=int(report.totals["followers"])
                        if report.totals.get("followers") is not None
                        else None,
                        following=int(report.totals["following"])
                        if report.totals.get("following") is not None
                        else None,
                        views=int(report.totals["views"])
                        if report.totals.get("views") is not None
                        else None,
                        posts=int(report.totals["posts"])
                        if report.totals.get("posts") is not None
                        else None,
                        likes=int(report.totals["likes"])
                        if report.totals.get("likes") is not None
                        else None,
                        engagement_rate=float(report.totals["engagement_rate"])
                        if report.totals.get("engagement_rate") is not None
                        else None,
                        source=report.data_source,
                    )
                )

            for pt in points_to_write:
                norm_date = normalize_date_str(pt.date)
                if not norm_date:
                    continue
                conn.execute(
                    """
                    INSERT INTO daily_snapshots (
                        canonical_id, snapshot_date, followers, following,
                        views, posts, likes, engagement_rate, source, fetched_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(canonical_id, snapshot_date) DO UPDATE SET
                        followers = COALESCE(excluded.followers, daily_snapshots.followers),
                        following = COALESCE(excluded.following, daily_snapshots.following),
                        views = COALESCE(excluded.views, daily_snapshots.views),
                        posts = COALESCE(excluded.posts, daily_snapshots.posts),
                        likes = COALESCE(excluded.likes, daily_snapshots.likes),
                        engagement_rate = COALESCE(excluded.engagement_rate, daily_snapshots.engagement_rate),
                        source = excluded.source,
                        fetched_at = excluded.fetched_at
                    """,
                    (
                        canonical_id,
                        norm_date,
                        pt.followers,
                        pt.following,
                        pt.views,
                        pt.posts,
                        pt.likes,
                        pt.engagement_rate,
                        pt.source or report.data_source,
                        now_iso,
                    ),
                )

        # Read-After-Write Verification: re-read from authoritative SQLite table
        prior_warning = report.completeness.truncation_warning if report.completeness else None
        verified_points = self.get_daily_snapshots(canonical_id, days=requested_days)
        report.daily = verified_points
        report.growth = enrich_growth_from_daily(verified_points, report.growth)
        report.completeness = build_completeness_receipt(
            requested_days=requested_days,
            returned_days=len(verified_points),
            platform=report.platform,
            data_source=report.data_source,
            pagination_verified=True,
            reason_override=prior_warning if (prior_warning and "HTTP 401" in prior_warning) else None,
        )
        report.ledger_verified = len(verified_points) > 0
        return report

    def resolve_canonical_id(self, platform: str, identifier: str) -> str | None:
        """Resolve a handle, username, or platform ID to its stored canonical_id."""
        clean = (identifier or "").strip().lstrip("@").lower()
        if not clean:
            return None
        alias_key = f"{platform.lower()}:{clean}"
        with self._connect() as conn:
            row = conn.execute(
                "SELECT canonical_id FROM creator_aliases WHERE alias_key = ?",
                (alias_key,),
            ).fetchone()
            if row:
                return str(row["canonical_id"])
            # Also check direct canonical_id match
            direct = conn.execute(
                "SELECT canonical_id FROM creators WHERE canonical_id = ?",
                (f"{platform.lower()}:{identifier.strip()}",),
            ).fetchone()
            if direct:
                return str(direct["canonical_id"])
        return None

    def get_daily_snapshots(self, canonical_id: str, days: int = 30) -> list[DailyPoint]:
        """Read the latest N daily snapshots for canonical_id and derive deltas."""
        limit = max(1, int(days))
        fetch_limit = limit + 1
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT snapshot_date, followers, following, views, posts, likes, engagement_rate, source
                FROM (
                    SELECT * FROM daily_snapshots
                    WHERE canonical_id = ?
                    ORDER BY snapshot_date DESC
                    LIMIT ?
                )
                ORDER BY snapshot_date ASC
                """,
                (canonical_id, fetch_limit),
            ).fetchall()

        raw_points = [
            DailyPoint(
                date=str(r["snapshot_date"]),
                followers=r["followers"],
                following=r["following"],
                views=r["views"],
                posts=r["posts"],
                likes=r["likes"],
                engagement_rate=r["engagement_rate"],
                source=str(r["source"]),
            )
            for r in rows
        ]
        derived = derive_daily_deltas(raw_points)
        if len(derived) > limit:
            return derived[-limit:]
        return derived

    def get_creator_summary(self, canonical_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT c.*,
                       COUNT(s.snapshot_date) AS snapshot_count,
                       MIN(s.snapshot_date) AS first_snapshot_date,
                       MAX(s.snapshot_date) AS last_snapshot_date
                FROM creators c
                LEFT JOIN daily_snapshots s ON c.canonical_id = s.canonical_id
                WHERE c.canonical_id = ?
                GROUP BY c.canonical_id
                """,
                (canonical_id,),
            ).fetchone()
            return dict(row) if row else None

    def list_tracked_creators(self, platform: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if platform:
                rows = conn.execute(
                    """
                    SELECT c.canonical_id, c.platform, c.platform_id, c.username, c.display_name,
                           c.handle, c.grade, c.sb_rank, c.last_source, c.updated_at,
                           COUNT(s.snapshot_date) AS snapshot_count,
                           MIN(s.snapshot_date) AS first_date,
                           MAX(s.snapshot_date) AS last_date
                    FROM creators c
                    LEFT JOIN daily_snapshots s ON c.canonical_id = s.canonical_id
                    WHERE c.platform = ?
                    GROUP BY c.canonical_id
                    ORDER BY c.updated_at DESC
                    """,
                    (platform.lower(),),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT c.canonical_id, c.platform, c.platform_id, c.username, c.display_name,
                           c.handle, c.grade, c.sb_rank, c.last_source, c.updated_at,
                           COUNT(s.snapshot_date) AS snapshot_count,
                           MIN(s.snapshot_date) AS first_date,
                           MAX(s.snapshot_date) AS last_date
                    FROM creators c
                    LEFT JOIN daily_snapshots s ON c.canonical_id = s.canonical_id
                    GROUP BY c.canonical_id
                    ORDER BY c.updated_at DESC
                    """
                ).fetchall()
        return [dict(r) for r in rows]

    def stats_summary(self) -> dict[str, Any]:
        with self._connect() as conn:
            c_count = conn.execute("SELECT COUNT(*) FROM creators").fetchone()[0]
            a_count = conn.execute("SELECT COUNT(*) FROM creator_aliases").fetchone()[0]
            s_count = conn.execute("SELECT COUNT(*) FROM daily_snapshots").fetchone()[0]
            by_plat = conn.execute(
                "SELECT platform, COUNT(*) AS cnt FROM creators GROUP BY platform ORDER BY cnt DESC"
            ).fetchall()
        return {
            "db_path": str(self.db_path),
            "exists": self.db_path.exists(),
            "creators_count": c_count,
            "aliases_count": a_count,
            "daily_snapshots_count": s_count,
            "by_platform": {str(r["platform"]): int(r["cnt"]) for r in by_plat},
        }
