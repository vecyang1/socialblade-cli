"""Input parser, platform classifier, and tier routing engine for Social Blade CLI.

Provides deterministic target resolution and full diagnostic explainability
(`socialblade explain <target>`). Every routing decision cites the exact rule ID
and rationale.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any

from .browser import resolve_backend
from .config import SocialBladeConfig
from .types import (
    MATRIX_FREE_SAMPLE_PLATFORMS,
    SOCIALBLADE_NATIVE_PLATFORMS,
    SUPPORTED_PLATFORMS,
    ParsedTarget,
)

PLATFORM_ALIASES: dict[str, str] = {
    "yt": "youtube",
    "youtube": "youtube",
    "tt": "tiktok",
    "tiktok": "tiktok",
    "ig": "instagram",
    "ins": "instagram",
    "instagram": "instagram",
    "tw": "twitch",
    "twitch": "twitch",
    "fb": "facebook",
    "facebook": "facebook",
    "x": "twitter",
    "twitter": "twitter",
}

YOUTUBE_CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
NUMERIC_ID_RE = re.compile(r"^\d{6,25}$")
SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")

YOUTUBE_RESERVED_PATHS = {
    "watch",
    "shorts",
    "playlist",
    "feed",
    "results",
    "gaming",
    "music",
    "premium",
    "hashtag",
    " live",
    "account",
    "reporthistory",
}

TWITTER_RESERVED_PATHS = {
    "home",
    "explore",
    "search",
    "notifications",
    "messages",
    "i",
    "settings",
    "compose",
    "intent",
    "hashtag",
    "tos",
    "privacy",
}

TIKTOK_RESERVED_PATHS = {
    "t",
    "v",
    "video",
    "discover",
    "tag",
    "music",
    "explore",
    "foryou",
    "following",
    "live",
    "upload",
    "messages",
    "search",
}

INSTAGRAM_RESERVED_PATHS = {
    "p",
    "reel",
    "reels",
    "tv",
    "explore",
    "accounts",
    "direct",
    "stories",
    "challenge",
}

TWITCH_RESERVED_PATHS = {
    "directory",
    "videos",
    "downloads",
    "jobs",
    "p",
    "search",
    "settings",
    "subscriptions",
    "wallet",
}

FACEBOOK_RESERVED_PATHS = {
    "watch",
    "reel",
    "reels",
    "marketplace",
    "groups",
    "events",
    "gaming",
    "login",
    "share",
    "sharer.php",
    "photo",
    "photo.php",
    "permalink.php",
    "story.php",
}


def _validate_identifier(ident: str, raw_input: str) -> str:
    """Validate that an extracted identifier is non-empty and free of injection/traversal tokens."""
    clean = (ident or "").strip().lstrip("@").strip("/")
    if not clean or clean in (".", "..") or ".." in clean:
        raise ValueError(f"Invalid or empty creator identifier in '{raw_input}'.")
    if not SAFE_IDENTIFIER_RE.match(clean):
        raise ValueError(
            f"Invalid characters in creator identifier '{clean}' (from '{raw_input}'). "
            "Only alphanumeric characters, '.', '_', and '-' are allowed."
        )
    return clean


def normalize_platform(raw_platform: str | None) -> str | None:
    if not raw_platform:
        return None
    key = raw_platform.strip().lower()
    return PLATFORM_ALIASES.get(key)


def build_socialblade_web_path(platform: str, identifier_type: str, identifier: str) -> str | None:
    """Build the canonical Social Blade web path for a parsed target."""
    if platform == "twitter":
        return None
    clean_id = _validate_identifier(identifier, identifier)
    if platform == "youtube":
        if identifier_type == "channel_id" or YOUTUBE_CHANNEL_ID_RE.match(clean_id):
            return f"/youtube/channel/{clean_id}"
        if identifier_type == "user":
            return f"/youtube/user/{clean_id}"
        return f"/youtube/handle/{clean_id}"
    if platform == "tiktok":
        return f"/tiktok/user/{clean_id}"
    if platform == "instagram":
        return f"/instagram/user/{clean_id}"
    if platform == "twitch":
        return f"/twitch/user/{clean_id}"
    if platform == "facebook":
        if identifier_type == "page" or NUMERIC_ID_RE.match(clean_id):
            return f"/facebook/page/{clean_id}"
        return f"/facebook/user/{clean_id}"
    return None


def parse_target(raw_input: str, explicit_platform: str | None = None) -> ParsedTarget:
    """Parse any URL, shorthand prefix, channel ID, or handle into a ParsedTarget."""
    raw = (raw_input or "").strip()
    if not raw:
        raise ValueError("Target input cannot be empty.")

    norm_explicit = normalize_platform(explicit_platform)
    if explicit_platform and not norm_explicit:
        raise ValueError(
            f"Unsupported platform '{explicit_platform}'. Supported: {', '.join(SUPPORTED_PLATFORMS)}"
        )

    # Rule 1: Full URL parsing (socialblade.com, youtube.com, x.com, twitter.com, tiktok.com, instagram.com, twitch.tv, facebook.com)
    if raw.startswith("http://") or raw.startswith("https://"):
        parsed_url = urllib.parse.urlparse(raw)
        host = (parsed_url.hostname or "").lower()
        path_parts = [urllib.parse.unquote(p) for p in parsed_url.path.split("/") if p]

        # 1a. Direct socialblade.com URL
        if host.endswith("socialblade.com"):
            if len(path_parts) < 2:
                raise ValueError(f"Social Blade URL is missing platform/creator path: {raw}")
            sb_plat = normalize_platform(path_parts[0])
            if not sb_plat:
                raise ValueError(f"Unsupported Social Blade platform '{path_parts[0]}' in URL: {raw}")
            if len(path_parts) >= 3 and path_parts[1] in ("handle", "channel", "user", "page", "c"):
                sub_type = path_parts[1]
                ident = _validate_identifier(path_parts[2], raw)
                id_type = (
                    "channel_id"
                    if sub_type == "channel"
                    else ("page" if sub_type == "page" else ("user" if sub_type == "user" else "handle"))
                )
            else:
                ident = _validate_identifier(path_parts[1], raw)
                id_type = "channel_id" if (sb_plat == "youtube" and YOUTUBE_CHANNEL_ID_RE.match(ident)) else "handle"
            web_path = build_socialblade_web_path(sb_plat, id_type, ident)
            return ParsedTarget(
                raw_input=raw,
                platform=sb_plat,
                identifier_type=id_type,
                identifier=ident,
                web_path=web_path,
                matched_rule_id="R1_SOCIALBLADE_URL",
                matched_rule_description=f"Parsed direct Social Blade URL for platform '{sb_plat}'",
            )

        # 1b. YouTube URL
        if host == "youtu.be" or host.endswith(".youtu.be"):
            raise ValueError(
                f"YouTube short video URL '{raw}' points to a video rather than a channel; pass a channel URL or @handle."
            )
        if "youtube.com" in host:
            if not path_parts:
                raise ValueError(f"YouTube URL is missing a channel or @handle path: {raw}")
            first_seg_lower = path_parts[0].lower()
            if first_seg_lower in YOUTUBE_RESERVED_PATHS:
                raise ValueError(
                    f"YouTube URL '{raw}' points to a '{path_parts[0]}' page rather than a creator channel."
                )
            if path_parts[0].startswith("@"):
                ident = _validate_identifier(path_parts[0], raw)
                return ParsedTarget(
                    raw_input=raw,
                    platform="youtube",
                    identifier_type="handle",
                    identifier=ident,
                    web_path=build_socialblade_web_path("youtube", "handle", ident),
                    matched_rule_id="R2_YOUTUBE_HANDLE_URL",
                    matched_rule_description="Extracted @handle from YouTube channel URL",
                )
            if first_seg_lower == "channel":
                if len(path_parts) < 2:
                    raise ValueError(f"YouTube channel URL is missing channel ID: {raw}")
                ident = _validate_identifier(path_parts[1], raw)
                return ParsedTarget(
                    raw_input=raw,
                    platform="youtube",
                    identifier_type="channel_id",
                    identifier=ident,
                    web_path=build_socialblade_web_path("youtube", "channel_id", ident),
                    matched_rule_id="R2_YOUTUBE_CHANNEL_ID_URL",
                    matched_rule_description="Extracted UC channel ID from YouTube URL",
                )
            if first_seg_lower in ("user", "c"):
                if len(path_parts) < 2:
                    raise ValueError(f"YouTube legacy URL is missing username: {raw}")
                ident = _validate_identifier(path_parts[1], raw)
                id_type = "user" if first_seg_lower == "user" else "handle"
                return ParsedTarget(
                    raw_input=raw,
                    platform="youtube",
                    identifier_type=id_type,
                    identifier=ident,
                    web_path=build_socialblade_web_path("youtube", id_type, ident),
                    matched_rule_id="R2_YOUTUBE_LEGACY_URL",
                    matched_rule_description=f"Extracted {id_type} from YouTube URL",
                )
            ident = _validate_identifier(path_parts[0], raw)
            return ParsedTarget(
                raw_input=raw,
                platform="youtube",
                identifier_type="handle",
                identifier=ident,
                web_path=build_socialblade_web_path("youtube", "handle", ident),
                matched_rule_id="R2_YOUTUBE_PATH_URL",
                matched_rule_description="Extracted first path segment from YouTube URL as handle",
            )

        # 1c. Twitter / X URL
        if host in ("x.com", "www.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"):
            if not path_parts:
                raise ValueError(f"Twitter/X URL is missing a username: {raw}")
            if path_parts[0].lower() in TWITTER_RESERVED_PATHS:
                raise ValueError(f"Twitter/X URL '{raw}' points to a reserved system path ('{path_parts[0]}').")
            ident = _validate_identifier(path_parts[0], raw)
            return ParsedTarget(
                raw_input=raw,
                platform="twitter",
                identifier_type="handle",
                identifier=ident,
                web_path=None,
                matched_rule_id="R3_TWITTER_URL",
                matched_rule_description="Extracted screen_name from X/Twitter URL (routed to TikHub bridge since Social Blade discontinued X)",
            )

        # 1d. TikTok URL
        if "tiktok.com" in host:
            if not path_parts:
                raise ValueError(f"TikTok URL is missing a @username path: {raw}")
            for seg in path_parts:
                if seg.startswith("@"):
                    ident = _validate_identifier(seg, raw)
                    return ParsedTarget(
                        raw_input=raw,
                        platform="tiktok",
                        identifier_type="user",
                        identifier=ident,
                        web_path=build_socialblade_web_path("tiktok", "user", ident),
                        matched_rule_id="R4_TIKTOK_URL",
                        matched_rule_description="Extracted @username from TikTok URL",
                    )
            if path_parts[0].lower() in TIKTOK_RESERVED_PATHS:
                raise ValueError(f"TikTok URL '{raw}' does not contain a creator @username.")
            ident = _validate_identifier(path_parts[0], raw)
            return ParsedTarget(
                raw_input=raw,
                platform="tiktok",
                identifier_type="user",
                identifier=ident,
                web_path=build_socialblade_web_path("tiktok", "user", ident),
                matched_rule_id="R4_TIKTOK_URL",
                matched_rule_description="Extracted username from TikTok URL",
            )

        # 1e. Instagram URL
        if "instagram.com" in host:
            if not path_parts:
                raise ValueError(f"Instagram URL is missing a username: {raw}")
            first_ig = path_parts[0].lower()
            if first_ig == "stories" and len(path_parts) >= 2:
                ident = _validate_identifier(path_parts[1], raw)
            elif first_ig in INSTAGRAM_RESERVED_PATHS:
                raise ValueError(f"Instagram URL '{raw}' points to a post/reel/system path rather than a profile.")
            else:
                ident = _validate_identifier(path_parts[0], raw)
            return ParsedTarget(
                raw_input=raw,
                platform="instagram",
                identifier_type="user",
                identifier=ident,
                web_path=build_socialblade_web_path("instagram", "user", ident),
                matched_rule_id="R5_INSTAGRAM_URL",
                matched_rule_description="Extracted username from Instagram URL",
            )

        # 1f. Twitch URL
        if "twitch.tv" in host:
            if not path_parts:
                raise ValueError(f"Twitch URL is missing a channel username: {raw}")
            if path_parts[0].lower() in TWITCH_RESERVED_PATHS:
                raise ValueError(f"Twitch URL '{raw}' points to a reserved system path ('{path_parts[0]}').")
            ident = _validate_identifier(path_parts[0], raw)
            return ParsedTarget(
                raw_input=raw,
                platform="twitch",
                identifier_type="user",
                identifier=ident,
                web_path=build_socialblade_web_path("twitch", "user", ident),
                matched_rule_id="R6_TWITCH_URL",
                matched_rule_description="Extracted broadcaster username from Twitch URL",
            )

        # 1g. Facebook URL
        if "facebook.com" in host or host in ("fb.com", "www.fb.com"):
            if path_parts and path_parts[0].lower() == "profile.php":
                qs_id = (urllib.parse.parse_qs(parsed_url.query).get("id") or [""])[0]
                ident = _validate_identifier(qs_id, raw)
            elif not path_parts:
                raise ValueError(f"Facebook URL is missing a page or user path: {raw}")
            elif path_parts[0].lower() in FACEBOOK_RESERVED_PATHS:
                raise ValueError(f"Facebook URL '{raw}' points to a post/video/system path rather than a page.")
            elif path_parts[0].lower() in ("pages", "pg", "p") and len(path_parts) >= 2:
                ident = _validate_identifier(path_parts[-1], raw)
            else:
                ident = _validate_identifier(path_parts[0], raw)
            id_type = "page" if NUMERIC_ID_RE.match(ident) else "user"
            return ParsedTarget(
                raw_input=raw,
                platform="facebook",
                identifier_type=id_type,
                identifier=ident,
                web_path=build_socialblade_web_path("facebook", id_type, ident),
                matched_rule_id="R7_FACEBOOK_URL",
                matched_rule_description="Extracted identifier from Facebook URL",
            )

        raise ValueError(f"Unrecognized social URL host '{host}' in '{raw}'.")

    # Rule 2: Shorthand "<platform>:<identifier>" or "<platform>/<identifier>"
    for sep in (":", "/"):
        if sep in raw:
            prefix, rest = raw.split(sep, 1)
            mapped_plat = normalize_platform(prefix)
            if mapped_plat:
                ident = _validate_identifier(rest, raw)
                if mapped_plat == "youtube" and YOUTUBE_CHANNEL_ID_RE.match(ident):
                    id_type = "channel_id"
                elif mapped_plat == "facebook" and NUMERIC_ID_RE.match(ident):
                    id_type = "page"
                elif mapped_plat in ("tiktok", "instagram", "twitch"):
                    id_type = "user"
                else:
                    id_type = "handle"
                return ParsedTarget(
                    raw_input=raw,
                    platform=mapped_plat,
                    identifier_type=id_type,
                    identifier=ident,
                    web_path=build_socialblade_web_path(mapped_plat, id_type, ident),
                    matched_rule_id="R8_PLATFORM_PREFIX",
                    matched_rule_description=f"Matched explicit '{prefix}{sep}<id>' prefix for platform '{mapped_plat}'",
                )

    # Rule 3: Bare YouTube UC... channel ID (24 chars starting with UC)
    clean = _validate_identifier(raw, raw)
    if YOUTUBE_CHANNEL_ID_RE.match(clean) and (norm_explicit in (None, "youtube")):
        return ParsedTarget(
            raw_input=raw,
            platform="youtube",
            identifier_type="channel_id",
            identifier=clean,
            web_path=build_socialblade_web_path("youtube", "channel_id", clean),
            matched_rule_id="R9_YOUTUBE_CHANNEL_ID",
            matched_rule_description="Detected 24-character YouTube UC* channel ID",
        )

    # Rule 4: Bare handle/username with explicit or default platform (youtube)
    platform = norm_explicit or "youtube"
    if platform == "facebook" and NUMERIC_ID_RE.match(clean):
        id_type = "page"
    elif platform in ("tiktok", "instagram", "twitch"):
        id_type = "user"
    else:
        id_type = "handle"

    rule_id = "R10_EXPLICIT_PLATFORM_HANDLE" if norm_explicit else "R11_DEFAULT_YOUTUBE_HANDLE"
    rule_desc = (
        f"Resolved handle '{clean}' under explicit platform '{platform}'"
        if norm_explicit
        else f"Resolved bare handle '{clean}' under default platform 'youtube'"
    )
    return ParsedTarget(
        raw_input=raw,
        platform=platform,
        identifier_type=id_type,
        identifier=clean,
        web_path=build_socialblade_web_path(platform, id_type, clean),
        matched_rule_id=rule_id,
        matched_rule_description=rule_desc,
    )


def select_tier_plan(
    target: ParsedTarget,
    config: SocialBladeConfig,
    requested_tier: str = "auto",
) -> dict[str, Any]:
    """Determine the execution tier order and explain the routing rationale."""
    tier_mode = (requested_tier or "auto").strip().lower()
    if tier_mode not in ("auto", "matrix", "ego", "web", "bridge"):
        raise ValueError(f"Invalid tier '{requested_tier}'. Valid: auto, matrix, ego, web, bridge")

    is_free_matrix_sample = (
        target.platform in MATRIX_FREE_SAMPLE_PLATFORMS
        and target.identifier.lower() == "socialblade"
    )
    can_use_matrix = config.has_matrix_credentials or is_free_matrix_sample
    browser_backend, browser_reason = resolve_backend(config)
    can_use_browser = browser_backend is not None

    # Special case: Twitter/X is completely discontinued on Social Blade
    if target.platform == "twitter":
        if tier_mode in ("matrix", "ego", "web"):
            return {
                "requested_tier": tier_mode,
                "primary_tier": "unsupported_on_socialblade",
                "fallback_tiers": ["platform_bridge"],
                "browser_backend": None,
                "can_execute": False,
                "rationale": (
                    "Social Blade discontinued Twitter/X tracking globally (returns 404 on both "
                    "Matrix API and Web). Use --tier auto or --tier bridge to route via TikHub + local SQLite ledger."
                ),
            }
        return {
            "requested_tier": tier_mode,
            "primary_tier": "platform_bridge",
            "fallback_tiers": [],
            "bridge_provider": (
                "tikhub_http + sqlite_ledger"
                if config.tikhub_api_key.value
                else "tikhub-twitter + sqlite_ledger"
            ),
            "browser_backend": None,
            "can_execute": True,
            "rationale": (
                "Social Blade discontinued Twitter/X support; automatically bridging to "
                "TikHub Twitter API + local SQLite time-series ledger for follower/tweet deltas."
            ),
        }

    if tier_mode == "matrix":
        return {
            "requested_tier": "matrix",
            "primary_tier": "matrix_api",
            "fallback_tiers": [],
            "browser_backend": None,
            "can_execute": can_use_matrix,
            "rationale": (
                "Explicit --tier matrix requested. "
                + (
                    "Using official Matrix API free test sample (query=socialblade)."
                    if is_free_matrix_sample and not config.has_matrix_credentials
                    else (
                        "Using configured SOCIALBLADE_CLIENT_ID + SOCIALBLADE_TOKEN."
                        if config.has_matrix_credentials
                        else "No Matrix API credentials configured; non-sample queries will return HTTP 401."
                    )
                )
            ),
        }

    if tier_mode in ("ego", "web"):
        return {
            "requested_tier": tier_mode,
            "primary_tier": "ego_trpc",
            "fallback_tiers": [],
            "browser_backend": browser_backend,
            "browser_backend_reason": browser_reason,
            "can_execute": can_use_browser,
            "rationale": (
                f"Explicit --tier {tier_mode} requested (browser_backend={browser_backend or 'none'}: {browser_reason}). "
                f"Extracts Next.js __NEXT_DATA__ and batches /api/trpc/{target.platform}.history + {target.platform}.growth."
            ),
        }

    if tier_mode == "bridge":
        return {
            "requested_tier": "bridge",
            "primary_tier": "platform_bridge",
            "fallback_tiers": [],
            "browser_backend": None,
            "can_execute": target.platform in ("youtube", "twitter"),
            "rationale": (
                f"Explicit --tier bridge requested for platform '{target.platform}' "
                "(uses yt-data for YouTube or TikHub for Twitter + local SQLite ledger)."
            ),
        }

    # Auto mode:
    chain: list[str] = []
    reasons: list[str] = []

    if can_use_matrix:
        chain.append("matrix_api")
        if is_free_matrix_sample and not config.has_matrix_credentials:
            reasons.append(
                "Target matches Social Blade's free official Business API test query ('socialblade'); Tier 1 matrix_api selected first (<300ms)."
            )
        else:
            reasons.append(
                "Matrix API credentials are configured; Tier 1 matrix_api selected first."
            )

    if target.platform in SOCIALBLADE_NATIVE_PLATFORMS and can_use_browser:
        chain.append("ego_trpc")
        reasons.append(
            f"Tier 2 ego_trpc (browser_backend={browser_backend}: {browser_reason}) uses origin anchor on socialblade.com to extract SSR __NEXT_DATA__ and /api/trpc/{target.platform}.history (30d) + {target.platform}.growth for free without Cloudflare 403 blocks."
        )

    if target.platform == "youtube" and config.yt_data_bin.value:
        chain.append("platform_bridge")
        reasons.append(
            "Tier 3 platform_bridge (yt-data + local SQLite ledger) registered as zero-auth fallback."
        )

    return {
        "requested_tier": "auto",
        "primary_tier": chain[0] if chain else "none",
        "fallback_tiers": chain[1:] if len(chain) > 1 else [],
        "browser_backend": browser_backend,
        "browser_backend_reason": browser_reason,
        "can_execute": len(chain) > 0,
        "rationale": " ".join(reasons) if reasons else f"No eligible provider available ({browser_reason}).",
    }


def select_posts_plan(
    target: ParsedTarget,
    config: SocialBladeConfig,
) -> dict[str, Any]:
    """Determine the execution plan for historical post/video/tweet engagement retrieval."""
    if target.platform == "youtube":
        has_yt = bool(config.yt_data_bin.value)
        return {
            "platform": "youtube",
            "primary_provider": "yt_data_channel" if has_yt else "sqlite_ssot",
            "can_execute": has_yt,
            "rationale": (
                "Social Blade gates youtube.videos behind authenticated accounts (HTTP 401 UNAUTHORIZED); "
                "bridges to yt-data channel stream + local SQLite creator_posts SSOT with derived engagement_rate."
                if has_yt
                else "yt-data binary not found on PATH; falls back to cached creator_posts in SQLite SSOT."
            ),
        }
    if target.platform == "twitter":
        has_th = bool(config.tikhub_api_key.value or config.tikhub_twitter_bin.value)
        return {
            "platform": "twitter",
            "primary_provider": (
                "tikhub_http_tweets"
                if config.tikhub_api_key.value
                else ("tikhub_cli_tweets" if config.tikhub_twitter_bin.value else "sqlite_ssot")
            ),
            "can_execute": has_th,
            "rationale": (
                "Social Blade discontinued Twitter/X globally; bridges to TikHub user tweets API/CLI "
                "+ local SQLite creator_posts SSOT with derived engagement_rate."
                if has_th
                else "Neither TIKHUB_API_KEY nor tikhub-twitter found; falls back to cached creator_posts in SQLite SSOT."
            ),
        }
    return {
        "platform": target.platform,
        "primary_provider": "sqlite_ssot",
        "can_execute": True,
        "rationale": (
            f"Social Blade does not expose public per-post engagement streams for '{target.platform}'; "
            "queries local SQLite creator_posts SSOT if previously ingested."
        ),
    }
