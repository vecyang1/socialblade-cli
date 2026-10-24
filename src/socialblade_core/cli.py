"""Unified CLI front door for Social Blade & Cross-Platform Creator Growth Analytics."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Sequence

from .browser import backend_availability, resolve_backend
from .config import SocialBladeConfig
from .ledger import SnapshotLedger, build_completeness_receipt, enrich_growth_from_daily
from .providers import (
    ProviderError,
    execute_stats_flow,
    fetch_ego_favorites,
    fetch_matrix_api,
)
from .router import (
    PLATFORM_ALIASES,
    SUPPORTED_PLATFORMS,
    normalize_platform,
    parse_target,
    select_tier_plan,
)
from .types import CreatorStatsReport, ParsedTarget


def _fmt_int(val: int | float | None, signed: bool = False) -> str:
    if val is None:
        return "--"
    if isinstance(val, float) and not val.is_integer():
        return f"{val:+,.2f}" if signed else f"{val:,.2f}"
    ival = int(val)
    if signed:
        return f"{ival:+,}" if ival != 0 else "0"
    return f"{ival:,}"


def _resolve_positional_target(
    pos_args: list[str],
    explicit_platform: str | None = None,
) -> ParsedTarget:
    """Support both `socialblade stats youtube @vecyang` and `socialblade stats @vecyang`."""
    if not pos_args:
        raise ValueError("Missing target argument (e.g., '@vecyang', 'youtube @vecyang', or a full URL).")
    if len(pos_args) == 1:
        return parse_target(pos_args[0], explicit_platform=explicit_platform)
    first_norm = normalize_platform(pos_args[0])
    if first_norm and not explicit_platform:
        return parse_target(pos_args[1], explicit_platform=first_norm)
    return parse_target(pos_args[0], explicit_platform=explicit_platform)


def _print_report_human(report: CreatorStatsReport, show_monthly: bool = False) -> None:
    fol_label = (
        "Subscribers"
        if report.platform == "youtube"
        else ("Likes" if report.platform == "facebook" else "Followers")
    )
    view_label = (
        "Talking About"
        if report.platform == "facebook"
        else ("Likes" if report.platform == "tiktok" else "Views")
    )
    post_label = (
        "Tweets"
        if report.platform == "twitter"
        else ("Media" if report.platform == "instagram" else "Videos")
    )

    print(f"📊 Creator Growth Report: {report.display_name} ({report.handle or report.username})")
    print("=" * 78)
    print(f"  • Canonical ID (SSOT): {report.canonical_id}")
    print(f"  • Platform:            {report.platform.upper()} (ID: {report.platform_id})")
    print(f"  • Data Source:         {report.data_source} (Ledger Verified: {'YES' if report.ledger_verified else 'NO'})")
    if report.fallback_reason:
        print(f"  • Routing Note:        {report.fallback_reason}")
    if report.grade or report.ranks.get("sb"):
        sb_rank_str = f"#{report.ranks['sb']:,}" if report.ranks.get("sb") else "--"
        fol_rank_str = f"#{report.ranks['followers']:,}" if report.ranks.get("followers") else "--"
        print(f"  • Grade & Ranks:       Grade {report.grade or '--'} | SB Rank {sb_rank_str} | {fol_label} Rank {fol_rank_str}")
    if report.country or report.category or report.created_at:
        print(
            f"  • Profile Meta:        Country: {report.country or '--'} | Category: {report.category or '--'} | Created: {report.created_at or '--'}"
        )

    t_fol = report.totals.get("followers")
    t_view = report.totals.get("views") if report.platform != "tiktok" else report.totals.get("likes")
    t_post = report.totals.get("posts")
    print("-" * 78)
    print(
        f"  • Current Totals:      {fol_label}: {_fmt_int(t_fol)} | {view_label}: {_fmt_int(t_view)} | {post_label}: {_fmt_int(t_post)}"
    )
    if report.totals.get("engagement_rate") is not None:
        print(f"  • Engagement Rate:     {report.totals['engagement_rate']:.2f}%")

    g = report.growth
    print("-" * 78)
    print("📈 Multi-Window Growth Summary (Derived & Reconciled):")
    print(
        f"  • {fol_label:<12} 1d: {_fmt_int(g.followers_1d, True):>10} | 7d: {_fmt_int(g.followers_7d, True):>10} | "
        f"14d: {_fmt_int(g.followers_14d, True):>10} | 30d: {_fmt_int(g.followers_30d, True):>10} | 365d: {_fmt_int(g.followers_365d, True):>10}"
    )
    if any(
        x is not None
        for x in (g.views_1d, g.views_7d, g.views_14d, g.views_30d, g.views_365d)
    ):
        print(
            f"  • {view_label:<12} 1d: {_fmt_int(g.views_1d, True):>10} | 7d: {_fmt_int(g.views_7d, True):>10} | "
            f"14d: {_fmt_int(g.views_14d, True):>10} | 30d: {_fmt_int(g.views_30d, True):>10} | 365d: {_fmt_int(g.views_365d, True):>10}"
        )
    if g.daily_avg_followers is not None or g.weekly_avg_followers is not None:
        print(
            f"  • Averages:      Daily {fol_label}: {_fmt_int(g.daily_avg_followers, True)}/day "
            f"({view_label}: {_fmt_int(g.daily_avg_views, True)}/day) | "
            f"Weekly {fol_label}: {_fmt_int(g.weekly_avg_followers, True)}/wk"
        )

    c = report.completeness
    print("-" * 78)
    status_badge = "✅ COMPLETE" if c.is_complete else "⚠️ PARTIAL WINDOW"
    print(
        f"📅 Daily Time Series ({c.returned_days}/{c.requested_days} days returned — {status_badge})"
    )
    if c.truncation_warning:
        print(f"   Note: {c.truncation_warning}")

    if report.daily:
        print(
            f"  {'Date':<12} | {fol_label:>14} | {'Δ ' + fol_label:>13} | {view_label:>15} | {'Δ ' + view_label:>13} | {post_label:>8}"
        )
        print("  " + "-" * 74)
        for pt in report.daily[-15:]:
            v_val = pt.likes if report.platform == "tiktok" else pt.views
            print(
                f"  {pt.date:<12} | {_fmt_int(pt.followers):>14} | {_fmt_int(pt.delta_followers, True):>13} | "
                f"{_fmt_int(v_val):>15} | {_fmt_int(pt.delta_views, True):>13} | {_fmt_int(pt.posts):>8}"
            )
        if len(report.daily) > 15:
            print(
                f"  ... ({len(report.daily) - 15} earlier daily rows stored in SQLite; pass --json for full {len(report.daily)}-day array)"
            )

    if show_monthly and report.monthly:
        print("-" * 78)
        print(f"📆 Monthly Gained History ({len(report.monthly)} months):")
        print(f"  {'Month':<12} | {'Gained ' + fol_label:>18} | {'Gained ' + view_label:>18} | {'New ' + post_label:>12}")
        print("  " + "-" * 68)
        for mp in report.monthly[-12:]:
            print(
                f"  {mp.date:<12} | {_fmt_int(mp.gained_followers, True):>18} | "
                f"{_fmt_int(mp.gained_views, True):>18} | {_fmt_int(mp.gained_posts, True):>12}"
            )
    print("=" * 78)


def cmd_stats(args: argparse.Namespace) -> int:
    config = SocialBladeConfig.resolve(
        cli_db_path=args.db_path,
        cli_client_id=args.client_id,
        cli_token=args.token,
        cli_days=args.days,
        cli_browser_backend=getattr(args, "browser_backend", None),
        cli_cdp_url=getattr(args, "cdp_url", None),
    )
    try:
        target = _resolve_positional_target(args.target, explicit_platform=args.platform)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    ledger = SnapshotLedger(config.db_path.value)
    days = int(config.default_days.value)
    try:
        report = execute_stats_flow(
            target,
            config,
            ledger,
            days=days,
            include_monthly=bool(args.monthly),
            requested_tier=args.tier,
            persist=not bool(args.no_save),
        )
    except ProviderError as exc:
        if args.json:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": str(exc),
                        "status_code": exc.status_code,
                        "tier": exc.tier,
                        "target": target.as_dict(),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(f"❌ Error retrieving stats for '{target.raw_input}': {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    else:
        _print_report_human(report, show_monthly=bool(args.monthly))
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    if len(args.targets) < 2:
        print("Error: 'compare' requires at least 2 targets.", file=sys.stderr)
        return 2

    config = SocialBladeConfig.resolve(
        cli_db_path=args.db_path,
        cli_days=args.days,
        cli_browser_backend=getattr(args, "browser_backend", None),
        cli_cdp_url=getattr(args, "cdp_url", None),
    )
    ledger = SnapshotLedger(config.db_path.value)
    days = int(config.default_days.value)

    reports: list[CreatorStatsReport] = []
    errors: list[dict[str, str]] = []

    for raw_t in args.targets:
        try:
            t = parse_target(raw_t, explicit_platform=args.platform)
            rep = execute_stats_flow(
                t,
                config,
                ledger,
                days=days,
                include_monthly=False,
                requested_tier=args.tier,
                persist=True,
            )
            reports.append(rep)
        except Exception as exc:
            errors.append({"target": raw_t, "error": str(exc)})

    if args.json:
        print(
            json.dumps(
                {
                    "compared_count": len(reports),
                    "requested_days": days,
                    "reports": [r.as_dict() for r in reports],
                    "errors": errors,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if reports else 1

    print(f"⚖️  Cross-Platform Creator Comparison (Window: {days}d)")
    print("=" * 102)
    print(
        f"  {'Creator (Canonical ID)':<34} | {'Plat':<8} | {'Grade':<5} | {'Followers/Subs':>14} | {'30d Δ Fol':>11} | {'30d Δ Views':>13} | {'Source'}"
    )
    print("  " + "-" * 98)
    for r in reports:
        label = f"{r.display_name[:16]} ({r.canonical_id})"[:34]
        print(
            f"  {label:<34} | {r.platform:<8} | {(r.grade or '--'):<5} | "
            f"{_fmt_int(r.totals.get('followers')):>14} | {_fmt_int(r.growth.followers_30d, True):>11} | "
            f"{_fmt_int(r.growth.views_30d, True):>13} | {r.data_source}"
        )
    if errors:
        print("-" * 102)
        for e in errors:
            print(f"  ⚠️ Failed: {e['target']} -> {e['error']}")
    print("=" * 102)
    return 0 if reports else 1


def cmd_favorites(args: argparse.Namespace) -> int:
    config = SocialBladeConfig.resolve(
        cli_db_path=args.db_path,
        cli_browser_backend=getattr(args, "browser_backend", None),
        cli_cdp_url=getattr(args, "cdp_url", None),
    )
    plat = normalize_platform(args.platform) or "youtube"
    try:
        items = fetch_ego_favorites(plat, config)
    except ProviderError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        else:
            print(f"❌ Error fetching Social Blade favorites: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(
            json.dumps(
                {"platform": plat, "count": len(items), "favorites": items},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    print(f"⭐ Social Blade Authenticated Favorites ({plat.upper()} — {len(items)} items)")
    print("-" * 78)
    for item in items:
        cid = item.get("id") or "--"
        name = item.get("displayName") or item.get("username") or "--"
        subs = _fmt_int(item.get("subscribers") or item.get("followers"))
        views = _fmt_int(int(item["views"])) if item.get("views") and str(item["views"]).isdigit() else (item.get("views") or "--")
        print(f"  • {name:<24} | ID: {cid:<26} | Subs: {subs:>10} | Views: {views}")
    print("-" * 78)
    return 0


def cmd_ledger(args: argparse.Namespace) -> int:
    config = SocialBladeConfig.resolve(
        cli_db_path=args.db_path,
        cli_days=args.days,
        cli_browser_backend=getattr(args, "browser_backend", None),
        cli_cdp_url=getattr(args, "cdp_url", None),
    )
    ledger = SnapshotLedger(config.db_path.value)
    action = (args.action or "list").lower()

    if action == "list":
        plat = normalize_platform(args.platform) if args.platform else None
        creators = ledger.list_tracked_creators(platform=plat)
        summary = ledger.stats_summary()
        if args.json:
            print(
                json.dumps(
                    {"summary": summary, "creators": creators},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        print(f"🗄️  SQLite Snapshot Ledger SSOT ({summary['db_path']})")
        print(
            f"   Tracked Creators: {summary['creators_count']} | Aliases: {summary['aliases_count']} | Daily Snapshots: {summary['daily_snapshots_count']}"
        )
        print("-" * 92)
        if not creators:
            print("  (No creators stored yet. Run `socialblade stats <target>` to record snapshots.)")
        else:
            print(
                f"  {'Canonical ID':<34} | {'Display Name':<20} | {'Snaps':>5} | {'Date Range':<23} | {'Last Source'}"
            )
            print("  " + "-" * 88)
            for c in creators:
                dr = f"{c.get('first_date') or '--'}..{c.get('last_date') or '--'}"
                print(
                    f"  {c['canonical_id']:<34} | {str(c.get('display_name') or '')[:20]:<20} | "
                    f"{int(c.get('snapshot_count') or 0):>5} | {dr:<23} | {c.get('last_source') or '--'}"
                )
        print("-" * 92)
        return 0

    if action == "history":
        if not args.target:
            print("Error: `socialblade ledger history <target>` requires a target.", file=sys.stderr)
            return 2
        parsed = parse_target(args.target, explicit_platform=args.platform)
        canonical_id = ledger.resolve_canonical_id(parsed.platform, parsed.identifier)
        if not canonical_id:
            print(
                f"Error: Target '{args.target}' ({parsed.platform}:{parsed.identifier}) is not in the local SQLite ledger yet.",
                file=sys.stderr,
            )
            return 1
        meta = ledger.get_creator_summary(canonical_id) or {}
        days = int(config.default_days.value)
        points = ledger.get_daily_snapshots(canonical_id, days=days)
        growth = enrich_growth_from_daily(points)
        completeness = build_completeness_receipt(
            requested_days=days,
            returned_days=len(points),
            platform=parsed.platform,
            data_source="sqlite_ssot",
        )
        payload = {
            "canonical_id": canonical_id,
            "creator": meta,
            "completeness": completeness.as_dict(),
            "derived_growth": growth.as_dict(),
            "daily": [p.as_dict() for p in points],
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        print(f"📜 Ledger History for {meta.get('display_name')} ({canonical_id})")
        print(
            f"   Snapshots Returned: {completeness.returned_days}/{completeness.requested_days} "
            f"({'COMPLETE' if completeness.is_complete else 'PARTIAL'})"
        )
        if completeness.truncation_warning:
            print(f"   Warning: {completeness.truncation_warning}")
        print("-" * 74)
        for pt in points:
            v_val = pt.views if pt.views is not None else pt.likes
            print(
                f"  {pt.date} | Followers: {_fmt_int(pt.followers):>12} (Δ {_fmt_int(pt.delta_followers, True):>8}) | "
                f"Views/Likes: {_fmt_int(v_val):>13} (Δ {_fmt_int(pt.delta_views, True):>9}) | Posts: {_fmt_int(pt.posts):>6}"
            )
        print("-" * 74)
        return 0

    print(f"Error: Unknown ledger action '{action}'. Use 'list' or 'history'.", file=sys.stderr)
    return 2


def cmd_explain(args: argparse.Namespace) -> int:
    config = SocialBladeConfig.resolve(
        cli_db_path=args.db_path,
        cli_days=args.days,
        cli_browser_backend=getattr(args, "browser_backend", None),
        cli_cdp_url=getattr(args, "cdp_url", None),
    )
    try:
        target = _resolve_positional_target(args.target, explicit_platform=args.platform)
        plan = select_tier_plan(target, config, requested_tier=args.tier)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    ledger = SnapshotLedger(config.db_path.value)
    known_canonical = ledger.resolve_canonical_id(target.platform, target.identifier)
    creator_meta = ledger.get_creator_summary(known_canonical) if known_canonical else None

    explanation = {
        "parsed_target": target.as_dict(),
        "routing_rule": {
            "rule_id": target.matched_rule_id,
            "description": target.matched_rule_description,
            "socialblade_supported": target.platform != "twitter",
            "socialblade_web_url": (
                f"{config.web_base_url.value}{target.web_path}"
                if target.web_path
                else None
            ),
        },
        "tier_execution_plan": plan,
        "pagination_and_window_contract": {
            "requested_days": config.default_days.value,
            "ssr_default_days": 15 if target.platform != "twitter" else 0,
            "trpc_expanded_days": 30 if target.platform != "twitter" else 0,
            "anti_truncation_guard": (
                "Calls /api/trpc/{platform}.history with limit>=30 to overcome Social Blade's 15-day SSR table truncation, "
                "and emits CompletenessReceipt.is_complete=False whenever returned_days < requested_days."
            ),
        },
        "local_ssot_ledger_state": {
            "db_path": str(config.db_path.value),
            "known_canonical_id": known_canonical,
            "stored_snapshots_count": int(creator_meta["snapshot_count"]) if creator_meta else 0,
            "first_snapshot_date": creator_meta.get("first_snapshot_date") if creator_meta else None,
            "last_snapshot_date": creator_meta.get("last_snapshot_date") if creator_meta else None,
        },
    }

    if args.json:
        print(json.dumps(explanation, ensure_ascii=False, indent=2))
        return 0

    print("🔍 Social Blade Diagnostic Explain (Target & Rule Trace)")
    print("=" * 78)
    print(f"  1. Input Recognized:    '{target.raw_input}'")
    print(f"     • Platform:          {target.platform}")
    print(f"     • Identifier Type:   {target.identifier_type}")
    print(f"     • Normalized ID:     {target.identifier}")
    print(
        f"     • Social Blade URL:  {explanation['routing_rule']['socialblade_web_url'] or 'N/A (Discontinued on Social Blade)'}"
    )
    print(f"  2. Matched Rule:        [{target.matched_rule_id}] {target.matched_rule_description}")
    print(f"  3. Selected Tier Plan:  Primary = {plan['primary_tier']} | Fallbacks = {plan.get('fallback_tiers', [])}")
    print(f"     • Rationale:         {plan['rationale']}")
    print(
        f"  4. Window & Pagination: Requested = {config.default_days.value}d (Expands 15d SSR to 30d via tRPC; flags partial windows)"
    )
    print(
        f"  5. Local SQLite SSOT:   Canonical ID = {known_canonical or '(Not yet recorded)'} "
        f"(Stored Snapshots: {explanation['local_ssot_ledger_state']['stored_snapshots_count']})"
    )
    print("=" * 78)
    return 0


def cmd_diagnose(args: argparse.Namespace) -> int:
    config = SocialBladeConfig.resolve(
        cli_db_path=args.db_path,
        cli_browser_backend=getattr(args, "browser_backend", None),
        cli_cdp_url=getattr(args, "cdp_url", None),
    )
    ledger = SnapshotLedger(config.db_path.value)
    ledger_info = ledger.stats_summary()

    # Probe Matrix API with official free test sample (youtube/statistics?query=socialblade)
    matrix_probe: dict[str, Any] = {"ok": False}
    try:
        sample_target = parse_target("youtube:socialblade")
        rep = fetch_matrix_api(sample_target, config, days=5)
        matrix_probe = {
            "ok": True,
            "sample_canonical_id": rep.canonical_id,
            "sample_subscribers": rep.totals.get("followers"),
            "daily_points_returned": len(rep.daily),
            "authenticated_mode": config.has_matrix_credentials,
        }
    except Exception as exc:
        matrix_probe = {"ok": False, "error": str(exc)}

    active_backend, backend_reason = resolve_backend(config)
    backends_map = backend_availability(config)

    diag = {
        "status": "ok" if (matrix_probe["ok"] and ledger_info["exists"]) else "degraded",
        "config": config.as_diagnostic_dict(),
        "subsystems": {
            "sqlite_ssot": {
                "status": "ok" if ledger_info["exists"] else "error",
                **ledger_info,
            },
            "tier1_matrix_api": matrix_probe,
            "tier2_ego_trpc": {
                "active_browser_backend": active_backend,
                "active_backend_reason": backend_reason,
                "backends_available": backends_map,
                "ego_browser_found": bool(config.ego_browser_bin.value),
                "ego_browser_path": config.ego_browser_bin.value or None,
                "task_space": config.ego_task_space.value,
                "supported_platforms": ["youtube", "tiktok", "instagram", "twitch", "facebook"],
            },
            "tier3_platform_bridges": {
                "twitter_x_bridge": {
                    "provider": "tikhub (API/CLI) + sqlite_ssot",
                    "available": bool(config.tikhub_api_key.value or config.tikhub_twitter_bin.value),
                    "tikhub_api_key_configured": bool(config.tikhub_api_key.value),
                    "binary": config.tikhub_twitter_bin.value or None,
                    "reason": "Bridges Twitter/X analytics discontinued by Social Blade",
                },
                "youtube_bridge": {
                    "provider": "yt-data + sqlite_ssot",
                    "available": bool(config.yt_data_bin.value),
                    "binary": config.yt_data_bin.value or None,
                    "reason": "Zero-browser fallback for YouTube channel totals",
                },
            },
        },
    }

    if args.json:
        print(json.dumps(diag, ensure_ascii=False, indent=2))
        return 0

    print("🩺 Social Blade CLI Diagnostic & Configuration Report")
    print("=" * 78)
    print(f"Overall Status: {diag['status'].upper()}")
    print("\n[1] Effective Configuration (Precedence: cli > env > default)")
    for k, v in diag["config"]["effective_values"].items():
        if "configured" in v:
            val_disp = f"configured={v['configured']} ({v['redacted']})"
        else:
            val_disp = str(v["value"])
        print(f"  • {k:<22}: {val_disp:<40} (source: {v['source']})")

    print("\n[2] Subsystem Health Probes")
    print(
        f"  • SQLite SSOT Ledger  : ✅ Ready ({ledger_info['creators_count']} creators, {ledger_info['daily_snapshots_count']} daily rows)"
    )
    if matrix_probe["ok"]:
        auth_tag = "Paid Key Active" if matrix_probe["authenticated_mode"] else "Free Sample Verified (query=socialblade)"
        print(f"  • Tier 1 Matrix API   : ✅ Reachable — {auth_tag}")
    else:
        print(f"  • Tier 1 Matrix API   : ⚠️ Error — {matrix_probe.get('error')}")

    print(
        f"  • Tier 2 Web Backend  : {'✅ Ready (' + active_backend + ')' if active_backend else '❌ Missing'} "
        f"(ego={backends_map['ego']['available']}, cdp={backends_map['cdp']['available']}, playwright={backends_map['playwright']['available']})"
    )
    th_ok = diag["subsystems"]["tier3_platform_bridges"]["twitter_x_bridge"]["available"]
    yt_ok = diag["subsystems"]["tier3_platform_bridges"]["youtube_bridge"]["available"]
    print(
        f"  • Tier 3 X/Twitter    : {'✅ Ready' if th_ok else '❌ Missing'} (TikHub API/CLI bridge for discontinued SB Twitter)"
    )
    print(
        f"  • Tier 3 YouTube      : {'✅ Ready' if yt_ok else '❌ Missing'} (yt-data zero-browser fallback)"
    )
    print("=" * 78)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="socialblade",
        description=(
            "Unified Social Blade & Cross-Platform Creator Growth CLI. "
            "Combines Official Matrix API (Tier 1), zero-cost ego-browser Next.js/tRPC 30d extraction (Tier 2), "
            "TikHub Twitter/X & yt-data bridges for capabilities Social Blade lacks (Tier 3), "
            "and a local SQLite SSOT snapshot ledger."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # 1. stats
    p_stats = sub.add_parser(
        "stats",
        help="Fetch creator growth statistics, rankings, grades, and 30-day daily table",
    )
    p_stats.add_argument(
        "target",
        nargs="+",
        help="Target URL, '<platform>:<handle>', or '<platform> <handle>' (e.g. 'youtube @vecyang', 'twitter @HTX_Studio')",
    )
    p_stats.add_argument(
        "-p",
        "--platform",
        choices=list(PLATFORM_ALIASES.keys()),
        help="Explicit platform override (youtube, tiktok, instagram, twitch, facebook, twitter/x)",
    )
    p_stats.add_argument(
        "-d",
        "--days",
        type=int,
        default=None,
        help="Number of daily history rows to request (default: 30)",
    )
    p_stats.add_argument(
        "--monthly",
        action="store_true",
        help="Include 30-month monthly gained series (YouTube)",
    )
    p_stats.add_argument(
        "--tier",
        choices=["auto", "matrix", "ego", "web", "bridge"],
        default="auto",
        help="Execution tier override (default: auto)",
    )
    p_stats.add_argument(
        "--browser-backend",
        choices=["auto", "ego", "cdp", "playwright"],
        default=None,
        help="Browser backend for Tier 2 web extraction (auto, ego, cdp, playwright)",
    )
    p_stats.add_argument("--cdp-url", default=None, help="CDP endpoint URL for 'cdp' browser backend")
    p_stats.add_argument(
        "--no-save",
        action="store_true",
        help="Skip persisting snapshot to the local SQLite SSOT ledger",
    )
    p_stats.add_argument("--db-path", default=None, help="Override SQLite SSOT database path")
    p_stats.add_argument("--client-id", default=None, help="Social Blade Matrix API clientid")
    p_stats.add_argument("--token", default=None, help="Social Blade Matrix API token")
    p_stats.add_argument("--json", action="store_true", help="Output structured JSON")
    p_stats.set_defaults(func=cmd_stats)

    # 2. compare
    p_cmp = sub.add_parser(
        "compare",
        help="Compare 2 or more creators side-by-side across platforms",
    )
    p_cmp.add_argument(
        "targets",
        nargs="+",
        help="2 or more targets (e.g. 'youtube:@vecyang' 'twitter:@HTX_Studio' 'youtube:socialblade')",
    )
    p_cmp.add_argument("-p", "--platform", default=None, help="Default platform for bare handles")
    p_cmp.add_argument("-d", "--days", type=int, default=None, help="Comparison window in days (default: 30)")
    p_cmp.add_argument("--tier", choices=["auto", "matrix", "ego", "web", "bridge"], default="auto")
    p_cmp.add_argument("--browser-backend", choices=["auto", "ego", "cdp", "playwright"], default=None)
    p_cmp.add_argument("--cdp-url", default=None, help="CDP endpoint URL for 'cdp' browser backend")
    p_cmp.add_argument("--db-path", default=None, help="Override SQLite SSOT database path")
    p_cmp.add_argument("--json", action="store_true", help="Output structured JSON")
    p_cmp.set_defaults(func=cmd_compare)

    # 3. favorites
    p_fav = sub.add_parser(
        "favorites",
        help="List saved Social Blade Favorites from the authenticated browser session",
    )
    p_fav.add_argument(
        "-p",
        "--platform",
        default="youtube",
        help="Platform to list favorites for (default: youtube)",
    )
    p_fav.add_argument("--browser-backend", choices=["auto", "ego", "cdp", "playwright"], default=None)
    p_fav.add_argument("--cdp-url", default=None, help="CDP endpoint URL for 'cdp' browser backend")
    p_fav.add_argument("--db-path", default=None, help="Override SQLite SSOT database path")
    p_fav.add_argument("--json", action="store_true", help="Output structured JSON")
    p_fav.set_defaults(func=cmd_favorites)

    # 4. ledger
    p_led = sub.add_parser(
        "ledger",
        help="Inspect the local SQLite SSOT snapshot ledger (list tracked creators or view stored history)",
    )
    p_led.add_argument(
        "action",
        nargs="?",
        default="list",
        choices=["list", "history"],
        help="Ledger action: 'list' (default) or 'history'",
    )
    p_led.add_argument("target", nargs="?", default=None, help="Target creator for 'history' action")
    p_led.add_argument("-p", "--platform", default=None, help="Platform filter or override")
    p_led.add_argument("-d", "--days", type=int, default=None, help="Days of history to retrieve")
    p_led.add_argument("--db-path", default=None, help="Override SQLite SSOT database path")
    p_led.add_argument("--json", action="store_true", help="Output structured JSON")
    p_led.set_defaults(func=cmd_ledger)

    # 5. explain
    p_exp = sub.add_parser(
        "explain",
        help="Diagnostic-first target parser & rule trace: explain how an input URL/handle is parsed and routed",
    )
    p_exp.add_argument("target", nargs="+", help="Target URL or handle to explain")
    p_exp.add_argument("-p", "--platform", default=None, help="Explicit platform override")
    p_exp.add_argument("--tier", choices=["auto", "matrix", "ego", "web", "bridge"], default="auto")
    p_exp.add_argument("--browser-backend", choices=["auto", "ego", "cdp", "playwright"], default=None)
    p_exp.add_argument("--cdp-url", default=None, help="CDP endpoint URL for 'cdp' browser backend")
    p_exp.add_argument("-d", "--days", type=int, default=None, help="Requested window in days")
    p_exp.add_argument("--db-path", default=None, help="Override SQLite SSOT database path")
    p_exp.add_argument("--json", action="store_true", help="Output structured JSON")
    p_exp.set_defaults(func=cmd_explain)

    # 6. diagnose
    p_diag = sub.add_parser(
        "diagnose",
        help="Inspect effective configuration, source precedence, Matrix API probe, browser backends, and SQLite SSOT health",
    )
    p_diag.add_argument("--browser-backend", choices=["auto", "ego", "cdp", "playwright"], default=None)
    p_diag.add_argument("--cdp-url", default=None, help="CDP endpoint URL for 'cdp' browser backend")
    p_diag.add_argument("--db-path", default=None, help="Override SQLite SSOT database path")
    p_diag.add_argument("--json", action="store_true", help="Output structured JSON")
    p_diag.set_defaults(func=cmd_diagnose)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
