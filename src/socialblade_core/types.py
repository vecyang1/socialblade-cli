"""Schema-first data contracts and types for Social Blade CLI.

All provider responses (Matrix API, ego-browser Next.js/tRPC, TikHub Twitter
bridge, yt-data bridge) normalize into these canonical structures.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

SUPPORTED_PLATFORMS = (
    "youtube",
    "tiktok",
    "instagram",
    "twitch",
    "facebook",
    "twitter",
)

SOCIALBLADE_NATIVE_PLATFORMS = (
    "youtube",
    "tiktok",
    "instagram",
    "twitch",
    "facebook",
)

MATRIX_FREE_SAMPLE_PLATFORMS = (
    "youtube",
    "instagram",
    "twitch",
    "facebook",
)


def to_optional_int(val: Any) -> int | None:
    if val is None or val == "" or val == "--":
        return None
    if isinstance(val, bool):
        return int(val)
    if isinstance(val, int):
        return val
    if isinstance(val, float):
        return int(val)
    s = str(val).strip().replace(",", "")
    if not s or s == "--":
        return None
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return None


def to_optional_float(val: Any) -> float | None:
    if val is None or val == "" or val == "--":
        return None
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return float(val)
    s = str(val).strip().replace(",", "").rstrip("%")
    if not s or s == "--":
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def normalize_date_str(raw: Any) -> str:
    """Normalize ISO timestamp or date string to YYYY-MM-DD."""
    s = str(raw or "").strip()
    if "T" in s:
        return s.split("T", 1)[0]
    if len(s) >= 10 and s[4:5] == "-" and s[7:8] == "-":
        return s[:10]
    return s


def sanitize_created_at(raw: Any) -> str | None:
    """Normalize creation date and filter out bogus 1970 Unix epoch artifacts."""
    norm = normalize_date_str(raw)
    if not norm or norm.startswith("1970-") or norm.startswith("1969-"):
        return None
    return norm


@dataclass(frozen=True)
class ParsedTarget:
    """Normalized creator query target and matched routing rule."""

    raw_input: str
    platform: str
    identifier_type: str  # "handle" | "channel_id" | "user" | "page"
    identifier: str
    web_path: str | None
    matched_rule_id: str
    matched_rule_description: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DailyPoint:
    """Single daily observation row.

    Note: delta_followers, delta_views, and delta_posts are DERIVED dynamically
    at read/render time from adjacent chronological snapshots (SSOT invariant:
    never persist redundant static deltas in SQLite).
    """

    date: str  # YYYY-MM-DD
    followers: int | None = None
    following: int | None = None
    views: int | None = None
    posts: int | None = None
    likes: int | None = None
    engagement_rate: float | None = None
    source: str = "unknown"
    delta_followers: int | None = None
    delta_views: int | None = None
    delta_posts: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MonthlyPoint:
    """Monthly gained metrics (returned by Social Blade tRPC monthly endpoint)."""

    date: str  # YYYY-MM-DD
    gained_followers: int | None = None
    gained_views: int | None = None
    gained_posts: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GrowthSummary:
    """Multi-window growth deltas (derived from time series + upstream growth summary)."""

    followers_1d: int | None = None
    followers_3d: int | None = None
    followers_7d: int | None = None
    followers_14d: int | None = None
    followers_30d: int | None = None
    followers_60d: int | None = None
    followers_90d: int | None = None
    followers_180d: int | None = None
    followers_365d: int | None = None
    followers_ytd: int | None = None
    views_1d: int | None = None
    views_3d: int | None = None
    views_7d: int | None = None
    views_14d: int | None = None
    views_30d: int | None = None
    views_60d: int | None = None
    views_90d: int | None = None
    views_180d: int | None = None
    views_365d: int | None = None
    views_ytd: int | None = None
    posts_30d: int | None = None
    daily_avg_followers: float | None = None
    daily_avg_views: float | None = None
    weekly_avg_followers: float | None = None
    weekly_avg_views: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CompletenessReceipt:
    """Explicit anti-truncation and pagination verification receipt."""

    requested_days: int
    returned_days: int
    is_complete: bool
    pagination_verified: bool
    truncation_warning: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CreatorStatsReport:
    """Unified cross-platform creator statistics & growth report."""

    canonical_id: str  # "<platform>:<immutable_platform_id>"
    platform: str
    platform_id: str
    username: str
    display_name: str
    handle: str | None = None
    created_at: str | None = None
    country: str | None = None
    category: str | None = None
    grade: str | None = None
    ranks: dict[str, int | None] = field(default_factory=dict)
    totals: dict[str, int | float | None] = field(default_factory=dict)
    growth: GrowthSummary = field(default_factory=GrowthSummary)
    daily: list[DailyPoint] = field(default_factory=list)
    monthly: list[MonthlyPoint] = field(default_factory=list)
    completeness: CompletenessReceipt = field(
        default_factory=lambda: CompletenessReceipt(
            requested_days=30,
            returned_days=0,
            is_complete=False,
            pagination_verified=False,
        )
    )
    data_source: str = "unknown"
    fallback_reason: str | None = None
    ledger_verified: bool = False
    raw_socials: list[dict[str, Any]] = field(default_factory=list)
    _untrimmed_daily: list[DailyPoint] = field(default_factory=list, repr=False)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("_untrimmed_daily", None)
        return d
