"""Multi-tier provider execution engine for Social Blade CLI.

Tier 1 (`matrix_api`):
    Official Social Blade Business API (`https://matrix.sbapis.com/b/{platform}/statistics`).
    Sub-300ms HTTP JSON when credentials (`clientid` + `token`) are configured
    or when querying the free test sample (`query=socialblade`).

Tier 2 (`ego_trpc`):
    Zero-cost, Cloudflare-proof extraction via `ego-browser` anchored on
    `https://socialblade.com/robots.txt` (zero ad-auction overhead). Parses SSR
    `__NEXT_DATA__` and batches `/api/trpc/{platform}.history` (30d) +
    `{platform}.growth` + `youtube.monthly` (30m).

Tier 3 (`platform_bridge`):
    Bridges capabilities that Social Blade lacks:
    - Twitter/X (discontinued by Social Blade): routes to `tikhub-twitter` +
      local SQLite snapshot ledger (`snapshots.db`).
    - YouTube zero-browser fallback: routes to `yt-data` + local SQLite ledger.
"""

from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from .browser import (
    BrowserError,
    FAVORITES_FN_JS,
    STATS_FN_JS,
    evaluate_on_socialblade,
)
from .config import SocialBladeConfig
from .ledger import (
    SnapshotLedger,
    build_completeness_receipt,
    derive_daily_deltas,
    enrich_growth_from_daily,
)
from .router import select_tier_plan
from .types import (
    CreatorStatsReport,
    DailyPoint,
    GrowthSummary,
    MonthlyPoint,
    ParsedTarget,
    normalize_date_str,
    sanitize_created_at,
    to_optional_float,
    to_optional_int,
)


class ProviderError(RuntimeError):
    """Raised when a provider tier fails or cannot resolve a target."""

    def __init__(self, message: str, *, status_code: int | None = None, tier: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.tier = tier


def _parse_matrix_response(
    payload: dict[str, Any],
    platform: str,
    requested_days: int,
) -> CreatorStatsReport:
    status_obj = payload.get("status", {})
    if not status_obj.get("success", False):
        err_msg = status_obj.get("error") or payload.get("message") or "Matrix API error"
        code = to_optional_int(status_obj.get("status")) or 400
        raise ProviderError(f"Matrix API error ({code}): {err_msg}", status_code=code, tier="matrix_api")

    data = payload.get("data") or {}
    id_block = data.get("id") or {}
    general = data.get("general") or {}
    stats = data.get("statistics") or {}
    totals_raw = stats.get("total") or {}
    growth_raw = stats.get("growth") or {}
    ranks_raw = data.get("ranks") or {}
    misc_raw = data.get("misc") or {}
    daily_raw = data.get("daily") or []

    platform_id = str(id_block.get("id") or id_block.get("username") or "").strip()
    username = str(id_block.get("username") or id_block.get("cusername") or platform_id).strip()
    display_name = str(id_block.get("display_name") or username).strip()
    handle = id_block.get("handle")
    canonical_id = f"{platform}:{platform_id}"

    # Extract platform-specific totals into canonical keys
    followers = to_optional_int(
        totals_raw.get("subscribers")
        if "subscribers" in totals_raw
        else (
            totals_raw.get("followers")
            if "followers" in totals_raw
            else totals_raw.get("likes")
        )
    )
    following = to_optional_int(totals_raw.get("following"))
    views = to_optional_int(
        totals_raw.get("views")
        if "views" in totals_raw
        else totals_raw.get("talking_about")
    )
    posts = to_optional_int(
        totals_raw.get("uploads")
        if "uploads" in totals_raw
        else totals_raw.get("media")
    )
    likes = to_optional_int(totals_raw.get("likes")) if platform != "facebook" else None
    engagement_rate = to_optional_float(totals_raw.get("engagement_rate"))

    # Growth block in Matrix API
    fol_growth = (
        growth_raw.get("subs")
        or growth_raw.get("followers")
        or growth_raw.get("likes")
        or {}
    )
    view_growth = (
        growth_raw.get("vidviews")
        or growth_raw.get("views")
        or growth_raw.get("talking_about")
        or {}
    )
    post_growth = (
        growth_raw.get("uploads")
        or growth_raw.get("media")
        or {}
    )

    growth = GrowthSummary(
        followers_1d=to_optional_int(fol_growth.get("1")),
        followers_3d=to_optional_int(fol_growth.get("3")),
        followers_7d=to_optional_int(fol_growth.get("7")),
        followers_14d=to_optional_int(fol_growth.get("14")),
        followers_30d=to_optional_int(fol_growth.get("30")),
        followers_60d=to_optional_int(fol_growth.get("60")),
        followers_90d=to_optional_int(fol_growth.get("90")),
        followers_180d=to_optional_int(fol_growth.get("180")),
        followers_365d=to_optional_int(fol_growth.get("365")),
        views_1d=to_optional_int(view_growth.get("1")),
        views_3d=to_optional_int(view_growth.get("3")),
        views_7d=to_optional_int(view_growth.get("7")),
        views_14d=to_optional_int(view_growth.get("14")),
        views_30d=to_optional_int(view_growth.get("30")),
        views_60d=to_optional_int(view_growth.get("60")),
        views_90d=to_optional_int(view_growth.get("90")),
        views_180d=to_optional_int(view_growth.get("180")),
        views_365d=to_optional_int(view_growth.get("365")),
        posts_30d=to_optional_int(post_growth.get("30")),
    )

    daily_pts: list[DailyPoint] = []
    for row in daily_raw:
        d_str = normalize_date_str(row.get("date"))
        if not d_str:
            continue
        r_fol = to_optional_int(
            row.get("subs")
            if "subs" in row
            else (row.get("followers") if "followers" in row else row.get("likes"))
        )
        r_views = to_optional_int(
            row.get("views") if "views" in row else row.get("talking_about")
        )
        r_posts = to_optional_int(
            row.get("uploads") if "uploads" in row else row.get("media")
        )
        daily_pts.append(
            DailyPoint(
                date=d_str,
                followers=r_fol,
                following=to_optional_int(row.get("following")),
                views=r_views,
                posts=r_posts,
                likes=to_optional_int(row.get("avg_likes")),
                engagement_rate=to_optional_float(row.get("engagement_rate")),
                source="matrix_api",
            )
        )

    untrimmed_daily = derive_daily_deltas(daily_pts)
    if requested_days and len(untrimmed_daily) > requested_days:
        trimmed_daily = untrimmed_daily[-requested_days:]
    else:
        trimmed_daily = list(untrimmed_daily)

    if likes is None and trimmed_daily and trimmed_daily[-1].likes is not None and platform != "facebook":
        likes = trimmed_daily[-1].likes

    growth = enrich_growth_from_daily(trimmed_daily, growth)
    grade_val = (misc_raw.get("grade") or {}).get("grade")
    geo = general.get("geo") or {}

    ranks = {
        "sb": to_optional_int(ranks_raw.get("sbrank")),
        "followers": to_optional_int(
            ranks_raw.get("subscribers")
            if "subscribers" in ranks_raw
            else (
                ranks_raw.get("followers")
                if "followers" in ranks_raw
                else ranks_raw.get("likes")
            )
        ),
        "views": to_optional_int(
            ranks_raw.get("views")
            if "views" in ranks_raw
            else ranks_raw.get("talking_about")
        ),
        "country": to_optional_int(ranks_raw.get("country")),
        "category": to_optional_int(ranks_raw.get("channel_type")),
    }

    completeness = build_completeness_receipt(
        requested_days=requested_days,
        returned_days=len(trimmed_daily),
        platform=platform,
        data_source="matrix_api",
        pagination_verified=True,
    )

    return CreatorStatsReport(
        canonical_id=canonical_id,
        platform=platform,
        platform_id=platform_id,
        username=username,
        display_name=display_name,
        handle=handle,
        created_at=sanitize_created_at(general.get("created_at")),
        country=geo.get("country_code") or geo.get("country") or None,
        category=general.get("channel_type") or None,
        grade=grade_val,
        ranks=ranks,
        totals={
            "followers": followers,
            "following": following,
            "views": views,
            "posts": posts,
            "likes": likes,
            "engagement_rate": engagement_rate,
        },
        growth=growth,
        daily=trimmed_daily,
        monthly=[],
        completeness=completeness,
        data_source="matrix_api",
        _untrimmed_daily=untrimmed_daily,
    )


def fetch_matrix_api(
    target: ParsedTarget,
    config: SocialBladeConfig,
    days: int = 30,
) -> CreatorStatsReport:
    """Fetch creator statistics from Official Social Blade Business Matrix API."""
    if target.platform == "twitter":
        raise ProviderError(
            "Social Blade discontinued Twitter/X on Matrix API (HTTP 404).",
            status_code=404,
            tier="matrix_api",
        )

    base = str(config.matrix_base_url.value).rstrip("/")
    query = urllib.parse.urlencode({"query": target.identifier})
    url = f"{base}/{target.platform}/statistics?{query}"

    headers = {
        "User-Agent": "socialblade-cli/1.0",
        "Accept": "application/json",
    }
    if config.matrix_client_id.value:
        headers["clientid"] = str(config.matrix_client_id.value)
    if config.matrix_token.value:
        headers["token"] = str(config.matrix_token.value)

    req = urllib.request.Request(url, headers=headers)
    timeout_sec = max(5.0, float(config.timeout_ms.value) / 1000.0)
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read().decode("utf-8")
            payload = json.loads(raw)
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="ignore")
        except Exception:
            pass
        raise ProviderError(
            f"Matrix API HTTP {exc.code}: {body[:200] or exc.reason}",
            status_code=exc.code,
            tier="matrix_api",
        ) from exc
    except Exception as exc:
        raise ProviderError(f"Matrix API request failed: {exc}", tier="matrix_api") from exc

    return _parse_matrix_response(payload, target.platform, requested_days=days)


def _run_ego_browser_script(js_code: str, config: SocialBladeConfig) -> dict[str, Any]:
    """Execute an inline Node.js script via `ego-browser nodejs` and parse its JSON stdout."""
    ego_bin = str(config.ego_browser_bin.value or "ego-browser")
    timeout_sec = max(15.0, (float(config.timeout_ms.value) / 1000.0) + 12.0)
    try:
        proc = subprocess.run(
            [ego_bin, "nodejs"],
            input=js_code,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProviderError(
            f"ego-browser timed out after {timeout_sec:.1f}s",
            tier="ego_trpc",
        ) from exc
    except FileNotFoundError as exc:
        raise ProviderError("ego-browser executable not found in PATH", tier="ego_trpc") from exc

    combined_out = f"{proc.stdout or ''}\n{proc.stderr or ''}"
    output_lines = [
        line.strip()
        for line in combined_out.splitlines()
        if line.strip() and not line.strip().startswith("[ego-browser:")
    ]
    if proc.returncode != 0 and not any(l.startswith("{") and l.endswith("}") for l in output_lines):
        err_out = (proc.stderr or proc.stdout or "").strip()
        raise ProviderError(
            f"ego-browser exited with code {proc.returncode}: {err_out[:300]}",
            tier="ego_trpc",
        )

    for line in reversed(output_lines):
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue

    joined = "\n".join(output_lines)
    try:
        return json.loads(joined)
    except Exception as exc:
        raise ProviderError(
            f"Failed to parse JSON from ego-browser output: {joined[:300]}",
            tier="ego_trpc",
        ) from exc


def fetch_ego_trpc(
    target: ParsedTarget,
    config: SocialBladeConfig,
    days: int = 30,
    include_monthly: bool = True,
) -> CreatorStatsReport:
    """Extract creator profile, 30-day history, growth deltas, and monthly series via browser backend."""
    if not target.web_path:
        raise ProviderError(
            f"Platform '{target.platform}' does not have a Social Blade web path.",
            status_code=404,
            tier="ego_trpc",
        )

    web_base = str(config.web_base_url.value or "https://socialblade.com").rstrip("/")
    timeout_ms = int(config.timeout_ms.value or 12000)
    # Request days + 1 (at least 31) so the oldest day in a 30-day window has a boundary row for delta derivation
    history_limit = max(31, int(days) + 1)

    payload_arg = {
        "webBase": web_base,
        "webPath": target.web_path,
        "platform": target.platform,
        "historyLimit": history_limit,
        "includeMonthly": bool(
            include_monthly
            and target.platform in ("youtube", "tiktok", "twitch", "facebook", "instagram")
        ),
        "timeoutMs": timeout_ms,
    }

    try:
        raw_res, backend_used = evaluate_on_socialblade(STATS_FN_JS, payload_arg, config)
    except BrowserError as exc:
        raise ProviderError(
            str(exc),
            status_code=exc.status_code,
            tier="ego_trpc",
        ) from exc

    if not raw_res.get("ok"):
        code = to_optional_int(raw_res.get("status")) or 500
        err_msg = str(raw_res.get("error") or "Unknown browser tRPC error")
        raise ProviderError(err_msg, status_code=code, tier="ego_trpc")

    u = raw_res.get("userData") or {}
    h_rows = raw_res.get("historyRows") or []
    g_raw = raw_res.get("growthData") or {}
    m_rows = raw_res.get("monthlyRows") or []
    trpc_auth_required = bool(raw_res.get("trpcAuthRequired"))

    platform = target.platform
    platform_id = str(u.get("id") or target.identifier).strip()
    username = str(
        u.get("username")
        or (u.get("identifier") or {}).get("identifier")
        or target.identifier
    ).strip()
    display_name = str(u.get("displayName") or username).strip()
    handle = (
        f"@{u['identifier']['identifier']}"
        if isinstance(u.get("identifier"), dict) and u["identifier"].get("identifier")
        else (f"@{username}" if username else None)
    )

    stats_block = u.get("stats") or u
    followers = to_optional_int(
        stats_block.get("subscribers")
        if "subscribers" in stats_block
        else (
            stats_block.get("followers")
            if "followers" in stats_block
            else stats_block.get("likes")
        )
    )
    following = to_optional_int(stats_block.get("following"))
    views = to_optional_int(
        stats_block.get("views")
        if "views" in stats_block
        else stats_block.get("talkingAbout")
    )
    posts = to_optional_int(
        stats_block.get("videos")
        if "videos" in stats_block
        else stats_block.get("mediaCount")
    )
    likes = (
        to_optional_int(
            stats_block.get("likes")
            if "likes" in stats_block
            else stats_block.get("averageLikes")
        )
        if platform != "facebook"
        else None
    )
    engagement_rate = to_optional_float(stats_block.get("engagementRate"))

    def _g_fol(suffix: str) -> int | None:
        for prefix in ("subscribers", "followers", "likes"):
            k = f"{prefix}{suffix}"
            if k in g_raw and g_raw[k] is not None:
                return to_optional_int(g_raw[k])
        return None

    def _g_view(suffix: str) -> int | None:
        for prefix in ("views", "likes", "talkingAbout"):
            k = f"{prefix}{suffix}"
            if k in g_raw and g_raw[k] is not None and not (prefix == "likes" and platform == "facebook"):
                return to_optional_int(g_raw[k])
        return None

    growth = GrowthSummary(
        followers_1d=_g_fol("1"),
        followers_3d=_g_fol("3"),
        followers_7d=_g_fol("7"),
        followers_14d=_g_fol("14"),
        followers_30d=_g_fol("30"),
        followers_60d=_g_fol("60"),
        followers_90d=_g_fol("90"),
        followers_180d=_g_fol("180"),
        followers_365d=_g_fol("365"),
        followers_ytd=_g_fol("Ytd"),
        views_1d=_g_view("1"),
        views_3d=_g_view("3"),
        views_7d=_g_view("7"),
        views_14d=_g_view("14"),
        views_30d=_g_view("30"),
        views_60d=_g_view("60"),
        views_90d=_g_view("90"),
        views_180d=_g_view("180"),
        views_365d=_g_view("365"),
        views_ytd=_g_view("Ytd"),
        posts_30d=to_optional_int(
            g_raw.get("videos30")
            if "videos30" in g_raw
            else g_raw.get("mediaCount30")
        ),
    )

    daily_pts: list[DailyPoint] = []
    for row in h_rows:
        d_str = normalize_date_str(row.get("date"))
        if not d_str:
            continue
        r_fol = to_optional_int(
            row.get("subscribers")
            if "subscribers" in row
            else (
                row.get("followers")
                if "followers" in row
                else row.get("likes")
            )
        )
        r_views = to_optional_int(
            row.get("views")
            if "views" in row
            else row.get("talking_about")
        )
        r_posts = to_optional_int(
            row.get("videos")
            if "videos" in row
            else row.get("media_count")
        )
        r_likes = (
            to_optional_int(
                row.get("likes")
                if "likes" in row
                else row.get("avg_likes")
            )
            if platform != "facebook"
            else None
        )
        daily_pts.append(
            DailyPoint(
                date=d_str,
                followers=r_fol,
                following=to_optional_int(row.get("following")),
                views=r_views,
                posts=r_posts,
                likes=r_likes,
                engagement_rate=to_optional_float(row.get("engagement_rate")),
                source="ego_trpc",
            )
        )

    # If tRPC history was empty (e.g., unauthenticated Instagram where history is gated behind login),
    # synthesize today's live point-in-time observation from SSR __NEXT_DATA__ totals.
    if not daily_pts and any(x is not None for x in (followers, following, views, posts, likes)):
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        daily_pts.append(
            DailyPoint(
                date=today_str,
                followers=followers,
                following=following,
                views=views,
                posts=posts,
                likes=likes,
                engagement_rate=engagement_rate,
                source="ego_trpc_ssr",
            )
        )

    untrimmed_daily = derive_daily_deltas(daily_pts)
    if days and len(untrimmed_daily) > days:
        trimmed_daily = untrimmed_daily[-days:]
    else:
        trimmed_daily = list(untrimmed_daily)

    growth = enrich_growth_from_daily(trimmed_daily, growth)

    monthly_pts: list[MonthlyPoint] = []
    seen_m_dates: set[str] = set()
    for row in m_rows:
        m_date = normalize_date_str(row.get("date"))
        if not m_date or m_date in seen_m_dates:
            continue
        seen_m_dates.add(m_date)
        m_fol = (
            row.get("subscribers")
            if "subscribers" in row
            else (
                row.get("followers")
                if "followers" in row
                else row.get("likes")
            )
        )
        m_views = (
            row.get("views")
            if "views" in row
            else (
                row.get("talking_about")
                if "talking_about" in row
                else (row.get("likes") if platform == "tiktok" else None)
            )
        )
        m_posts = (
            row.get("videos")
            if "videos" in row
            else (row.get("media_count") if "media_count" in row else row.get("media"))
        )
        monthly_pts.append(
            MonthlyPoint(
                date=m_date,
                gained_followers=to_optional_int(m_fol),
                gained_views=to_optional_int(m_views),
                gained_posts=to_optional_int(m_posts),
            )
        )

    ranks_raw = u.get("ranks") or {}
    ranks = {
        "sb": to_optional_int(ranks_raw.get("sb")),
        "followers": to_optional_int(
            ranks_raw.get("subscribers")
            if "subscribers" in ranks_raw
            else (
                ranks_raw.get("followers")
                if "followers" in ranks_raw
                else ranks_raw.get("likes")
            )
        ),
        "views": to_optional_int(
            ranks_raw.get("views")
            if "views" in ranks_raw
            else (
                ranks_raw.get("talkingAbout")
                if "talkingAbout" in ranks_raw
                else ranks_raw.get("engagementRate")
            )
        ),
        "country": to_optional_int(ranks_raw.get("country")),
        "category": to_optional_int(ranks_raw.get("category")),
    }

    auth_warning = (
        f"Incomplete window: returned {len(trimmed_daily)}/{days} daily points. "
        f"Social Blade gates {platform} historical tRPC endpoints ({platform}.history / {platform}.growth) "
        "behind a web account login (HTTP 401 UNAUTHORIZED); captured today's live SSR profile snapshot."
        if (trpc_auth_required and len(trimmed_daily) < days)
        else None
    )

    completeness = build_completeness_receipt(
        requested_days=days,
        returned_days=len(trimmed_daily),
        platform=platform,
        data_source="ego_trpc",
        pagination_verified=True,
        reason_override=auth_warning,
    )

    return CreatorStatsReport(
        canonical_id=f"{platform}:{platform_id}",
        platform=platform,
        platform_id=platform_id,
        username=username,
        display_name=display_name,
        handle=handle,
        created_at=sanitize_created_at(u.get("createdAt")),
        country=u.get("country") or None,
        category=u.get("channelType") or None,
        grade=u.get("grade") or None,
        ranks=ranks,
        totals={
            "followers": followers,
            "following": following,
            "views": views,
            "posts": posts,
            "likes": likes,
            "engagement_rate": engagement_rate,
        },
        growth=growth,
        daily=trimmed_daily,
        monthly=monthly_pts,
        completeness=completeness,
        data_source="ego_trpc",
        raw_socials=u.get("socials") if isinstance(u.get("socials"), list) else [],
        _untrimmed_daily=untrimmed_daily,
    )


def fetch_ego_favorites(
    platform: str,
    config: SocialBladeConfig,
) -> list[dict[str, Any]]:
    """Fetch the logged-in user's Social Blade Favorites list via browser backend tRPC."""
    try:
        raw, _ = evaluate_on_socialblade(FAVORITES_FN_JS, platform.lower(), config)
    except BrowserError as exc:
        raise ProviderError(
            str(exc),
            status_code=exc.status_code,
            tier="ego_trpc",
        ) from exc

    if not raw.get("ok", True):
        code = to_optional_int(raw.get("status")) or 401
        err_msg = str(raw.get("error") or "UNAUTHORIZED")
        raise ProviderError(
            f"Social Blade account favorites require an authenticated session ({err_msg}, HTTP {code}). "
            "Sign in to socialblade.com in your browser session or use `socialblade ledger list` for local SSOT tracked creators.",
            status_code=code,
            tier="ego_trpc",
        )
    items = raw.get("items")
    return items if isinstance(items, list) else []


def _fetch_tikhub_twitter_http(screen_name: str, config: SocialBladeConfig) -> dict[str, Any]:
    """Direct HTTP client for TikHub Twitter Web API when TIKHUB_API_KEY is configured."""
    base_url = str(
        config.tikhub_twitter_base_url.value
        or "https://api.tikhub.io/api/v1/twitter/web"
    ).rstrip("/")
    query = urllib.parse.urlencode({"screen_name": screen_name.lstrip("@")})
    url = f"{base_url}/fetch_user_profile?{query}"
    headers = {
        "Authorization": f"Bearer {config.tikhub_api_key.value}",
        "Accept": "application/json",
        "User-Agent": "socialblade-cli/1.1",
    }
    req = urllib.request.Request(url, headers=headers)
    timeout_sec = max(10.0, float(config.timeout_ms.value) / 1000.0)
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ProviderError(
            f"TikHub Twitter API HTTP {exc.code}: {exc.reason}",
            status_code=exc.code,
            tier="platform_bridge",
        ) from exc
    except Exception as exc:
        raise ProviderError(
            f"TikHub Twitter API request failed: {exc}",
            tier="platform_bridge",
        ) from exc

    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    return data if isinstance(data, dict) else {}


def fetch_platform_bridge(
    target: ParsedTarget,
    config: SocialBladeConfig,
    days: int = 30,
) -> CreatorStatsReport:
    """Bridge to TikHub (direct API or tikhub-twitter CLI for X/Twitter) or yt-data (for YouTube fallback)."""
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if target.platform == "twitter":
        user: dict[str, Any] | None = None
        tikhub_bin = str(config.tikhub_twitter_bin.value or "")
        if config.tikhub_api_key.value:
            try:
                user = _fetch_tikhub_twitter_http(target.identifier, config)
            except ProviderError:
                if not tikhub_bin:
                    raise

        if user is None:
            if not tikhub_bin:
                raise ProviderError(
                    "Neither TIKHUB_API_KEY nor tikhub-twitter CLI is configured for Twitter/X bridge.",
                    tier="platform_bridge",
                )
            try:
                proc = subprocess.run(
                    [tikhub_bin, "profile", target.identifier, "--json"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            except Exception as exc:
                raise ProviderError(f"tikhub-twitter execution failed: {exc}", tier="platform_bridge") from exc

            if proc.returncode != 0:
                raise ProviderError(
                    f"tikhub-twitter exited with {proc.returncode}: {(proc.stderr or proc.stdout).strip()[:250]}",
                    tier="platform_bridge",
                )

            try:
                user = json.loads(proc.stdout)
            except Exception as exc:
                raise ProviderError(f"Invalid JSON from tikhub-twitter: {exc}", tier="platform_bridge") from exc

        rest_id = str(user.get("rest_id") or user.get("id") or "").strip()
        if not rest_id or user.get("status") == "not_found":
            raise ProviderError(
                f"Twitter/X user '@{target.identifier}' not found via TikHub.",
                status_code=404,
                tier="platform_bridge",
            )

        followers = to_optional_int(
            user.get("sub_count")
            if user.get("sub_count") is not None
            else user.get("followers_count")
        )
        following = to_optional_int(user.get("friends_count"))
        posts = to_optional_int(user.get("statuses_count"))
        likes = to_optional_int(user.get("favourites_count"))
        display_name = str(user.get("name") or target.identifier).strip()
        username = str(user.get("screen_name") or target.identifier).strip()

        pt = DailyPoint(
            date=today_str,
            followers=followers,
            following=following,
            views=None,
            posts=posts,
            likes=likes,
            source="tikhub_twitter",
        )

        return CreatorStatsReport(
            canonical_id=f"twitter:{rest_id}",
            platform="twitter",
            platform_id=rest_id,
            username=username,
            display_name=display_name,
            handle=f"@{username}",
            created_at=user.get("created_at"),
            country=None,
            category=None,
            grade=None,
            ranks={},
            totals={
                "followers": followers,
                "following": following,
                "views": None,
                "posts": posts,
                "likes": likes,
                "engagement_rate": None,
            },
            growth=GrowthSummary(),
            daily=[pt],
            monthly=[],
            completeness=build_completeness_receipt(
                requested_days=days,
                returned_days=1,
                platform="twitter",
                data_source="tikhub_twitter+local_ledger",
            ),
            data_source="tikhub_twitter+local_ledger",
            fallback_reason=(
                "Social Blade discontinued Twitter/X tracking globally; bridged to "
                "tikhub-twitter + local SQLite snapshot ledger."
            ),
            _untrimmed_daily=[pt],
        )

    if target.platform == "youtube":
        yt_bin = str(config.yt_data_bin.value or "")
        if not yt_bin:
            raise ProviderError("yt-data CLI is not available for YouTube fallback", tier="platform_bridge")

        query_arg = (
            target.identifier
            if target.identifier_type == "channel_id"
            else f"@{target.identifier.lstrip('@')}"
        )
        try:
            proc = subprocess.run(
                [yt_bin, "inspect", query_arg, "--json"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except Exception as exc:
            raise ProviderError(f"yt-data execution failed: {exc}", tier="platform_bridge") from exc

        if proc.returncode != 0:
            raise ProviderError(
                f"yt-data inspect failed ({proc.returncode}): {(proc.stderr or proc.stdout).strip()[:250]}",
                status_code=404,
                tier="platform_bridge",
            )

        ch = json.loads(proc.stdout)
        channel_id = str(ch.get("channel_id") or target.identifier).strip()
        title = str(ch.get("title") or target.identifier).strip()
        handle = ch.get("handle") or f"@{target.identifier.lstrip('@')}"
        subs = to_optional_int(ch.get("subscriber_count"))
        views = to_optional_int(ch.get("view_count"))
        videos = to_optional_int(ch.get("video_count"))

        pt = DailyPoint(
            date=today_str,
            followers=subs,
            views=views,
            posts=videos,
            source="yt_data",
        )

        return CreatorStatsReport(
            canonical_id=f"youtube:{channel_id}",
            platform="youtube",
            platform_id=channel_id,
            username=handle.lstrip("@"),
            display_name=title,
            handle=handle,
            totals={
                "followers": subs,
                "following": None,
                "views": views,
                "posts": videos,
                "likes": None,
                "engagement_rate": None,
            },
            growth=GrowthSummary(),
            daily=[pt],
            monthly=[],
            completeness=build_completeness_receipt(
                requested_days=days,
                returned_days=1,
                platform="youtube",
                data_source="yt_data+local_ledger",
            ),
            data_source="yt_data+local_ledger",
            fallback_reason="Fallback to local yt-data CLI + SQLite snapshot ledger.",
            _untrimmed_daily=[pt],
        )

    raise ProviderError(
        f"No platform bridge available for '{target.platform}'.",
        tier="platform_bridge",
    )


def execute_stats_flow(
    target: ParsedTarget,
    config: SocialBladeConfig,
    ledger: SnapshotLedger,
    *,
    days: int = 30,
    include_monthly: bool = False,
    requested_tier: str = "auto",
    persist: bool = True,
) -> CreatorStatsReport:
    """Execute the end-to-end stats retrieval and SQLite Read-After-Write reconciliation."""
    plan = select_tier_plan(target, config, requested_tier=requested_tier)
    if not plan.get("can_execute"):
        raise ProviderError(
            str(plan.get("rationale") or "Cannot execute tier plan."),
            status_code=400,
            tier=requested_tier,
        )

    tiers_to_try = [plan["primary_tier"]] + list(plan.get("fallback_tiers") or [])
    errors: list[str] = []
    last_status_code: int | None = None
    report: CreatorStatsReport | None = None

    for idx, tier_name in enumerate(tiers_to_try):
        try:
            if tier_name == "matrix_api":
                report = fetch_matrix_api(target, config, days=days)
            elif tier_name == "ego_trpc":
                report = fetch_ego_trpc(
                    target,
                    config,
                    days=days,
                    include_monthly=include_monthly,
                )
            elif tier_name == "platform_bridge":
                report = fetch_platform_bridge(target, config, days=days)
            else:
                raise ProviderError(f"Unknown tier '{tier_name}'", tier=tier_name)

            if idx > 0 and errors:
                report.fallback_reason = (
                    f"Primary tier failed ({'; '.join(errors)}); recovered via {tier_name}."
                )
            break
        except ProviderError as exc:
            errors.append(f"{tier_name}: {exc}")
            last_status_code = exc.status_code or last_status_code
            # If target is definitively 404 on ego_trpc, don't mask a real 404 unless bridge is requested
            if exc.status_code == 404 and tier_name == "ego_trpc" and requested_tier != "auto":
                raise

    if report is None:
        raise ProviderError(
            "All configured tiers failed -> " + " | ".join(errors),
            status_code=last_status_code,
            tier=requested_tier,
        )

    if persist:
        report = ledger.upsert_report(
            report,
            requested_days=days,
            extra_aliases=[target.identifier],
        )
    else:
        report.daily = derive_daily_deltas(report.daily)
        report.growth = enrich_growth_from_daily(report.daily, report.growth)

    return report
