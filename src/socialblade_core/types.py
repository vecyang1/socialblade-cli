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
    """Normalize ISO timestamp, RFC-2822 Twitter timestamp, or YYYYMMDD to YYYY-MM-DD."""
    s = str(raw or "").strip()
    if not s:
        return ""
    if "T" in s:
        return s.split("T", 1)[0]
    if len(s) >= 10 and s[4:5] == "-" and s[7:8] == "-":
        return s[:10]
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    # Handle Twitter RFC-2822 timestamps such as "Wed Oct 21 16:31:06 +0000 2026"
    if any(m in s for m in ("Jan ", "Feb ", "Mar ", "Apr ", "May ", "Jun ", "Jul ", "Aug ", "Sep ", "Oct ", "Nov ", "Dec ")):
        try:
            from email.utils import parsedate_to_datetime

            dt = parsedate_to_datetime(s)
            return dt.strftime("%Y-%m-%d")
        except Exception:
            pass
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


@dataclass
class PostPoint:
    """Single historical post / video / tweet item with engagement metrics.

    Note: engagement_rate is DERIVED dynamically on read from
    (likes + comments + shares) / views * 100 (SSOT invariant: never persist
    redundant derived rate columns in SQLite `creator_posts`).
    """

    post_uid: str  # "<platform>:<post_id>"
    canonical_id: str  # "<platform>:<creator_id>"
    platform: str
    post_id: str
    published_date: str  # YYYY-MM-DD
    published_at: str | None = None
    title: str = ""
    url: str = ""
    post_type: str = "post"  # "video" | "short" | "tweet" | "post"
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    bookmarks: int | None = None
    duration: str | None = None
    source: str = "unknown"
    engagement_rate: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TimelineDayEntry:
    """Unified chronological day joining daily follower/view deltas with published posts."""

    date: str  # YYYY-MM-DD
    followers: int | None = None
    delta_followers: int | None = None
    views: int | None = None
    delta_views: int | None = None
    total_posts: int | None = None
    delta_posts: int | None = None
    published_posts_count: int = 0
    published_post_views: int = 0
    published_post_likes: int = 0
    published_post_comments: int = 0
    published_post_shares: int = 0
    published_engagement_rate: float | None = None
    published_posts: list[PostPoint] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CreatorTimelineReport:
    """Unified follower growth delta + historical post engagement timeline report."""

    canonical_id: str
    platform: str
    platform_id: str
    username: str
    display_name: str
    handle: str | None = None
    stats_source: str = "unknown"
    posts_source: str = "unknown"
    ledger_verified: bool = False
    completeness: CompletenessReceipt = field(
        default_factory=lambda: CompletenessReceipt(
            requested_days=30,
            returned_days=0,
            is_complete=False,
            pagination_verified=False,
        )
    )
    posts_completeness: CompletenessReceipt = field(
        default_factory=lambda: CompletenessReceipt(
            requested_days=20,
            returned_days=0,
            is_complete=False,
            pagination_verified=False,
        )
    )
    engagement_summary: dict[str, Any] = field(default_factory=dict)
    timeline: list[TimelineDayEntry] = field(default_factory=list)
    posts: list[PostPoint] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProjectionPoint:
    """Single future forecast point (Holt's linear or velocity projection)."""

    date: str  # YYYY-MM-DD
    horizon_days: int
    followers: int | None = None
    views: int | None = None
    gained_followers: int | None = None
    gained_views: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MilestoneCrossing:
    """Projected date when a creator crosses a round subscriber/follower or view milestone."""

    metric: str  # "followers" | "views"
    milestone: int
    projected_date: str
    days_until: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CreatorProjectionReport:
    """Future projections and milestone crossing report (up to 5 years / 1,826 days)."""

    canonical_id: str
    platform: str
    platform_id: str
    username: str
    display_name: str
    handle: str | None = None
    current_followers: int | None = None
    current_views: int | None = None
    method: str = "holts_linear"
    total_projected_days: int = 0
    horizons: list[ProjectionPoint] = field(default_factory=list)
    milestones: list[MilestoneCrossing] = field(default_factory=list)
    series: list[ProjectionPoint] = field(default_factory=list)
    data_source: str = "unknown"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SearchCandidate:
    """Single creator search match."""

    platform: str
    platform_id: str
    canonical_id: str
    username: str
    display_name: str
    handle: str | None = None
    followers: int | None = None
    views: int | None = None
    posts: int | None = None
    verified: bool | None = None
    source: str = "unknown"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SearchReport:
    """Creator discovery & search report."""

    query: str
    platform: str
    count: int
    candidates: list[SearchCandidate] = field(default_factory=list)
    data_source: str = "unknown"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

