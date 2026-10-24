"""Comprehensive TDD & Two-Sided Verification suite for Social Blade CLI."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys

_SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from socialblade_core.cli import main
from socialblade_core.config import SocialBladeConfig
from socialblade_core.ledger import (
    SnapshotLedger,
    build_completeness_receipt,
    derive_daily_deltas,
    enrich_growth_from_daily,
)
from socialblade_core.providers import ProviderError, _parse_matrix_response
from socialblade_core.router import (
    build_socialblade_web_path,
    parse_target,
    select_tier_plan,
)
from socialblade_core.types import CreatorStatsReport, DailyPoint, GrowthSummary


class TestTargetRouterAndExplain(unittest.TestCase):
    """Test legitimate and adversarial target parsing and tier selection."""

    def test_parse_youtube_urls_and_handles(self) -> None:
        t1 = parse_target("https://www.youtube.com/@vecyang/videos")
        self.assertEqual(t1.platform, "youtube")
        self.assertEqual(t1.identifier_type, "handle")
        self.assertEqual(t1.identifier, "vecyang")
        self.assertEqual(t1.web_path, "/youtube/handle/vecyang")
        self.assertEqual(t1.matched_rule_id, "R2_YOUTUBE_HANDLE_URL")

        t2 = parse_target("https://www.youtube.com/channel/UC8kOCg0Cf8Kw7b_YikLlAig")
        self.assertEqual(t2.platform, "youtube")
        self.assertEqual(t2.identifier_type, "channel_id")
        self.assertEqual(t2.identifier, "UC8kOCg0Cf8Kw7b_YikLlAig")
        self.assertEqual(t2.web_path, "/youtube/channel/UC8kOCg0Cf8Kw7b_YikLlAig")

        t3 = parse_target("UC8kOCg0Cf8Kw7b_YikLlAig")
        self.assertEqual(t3.platform, "youtube")
        self.assertEqual(t3.identifier_type, "channel_id")
        self.assertEqual(t3.matched_rule_id, "R9_YOUTUBE_CHANNEL_ID")

    def test_parse_socialblade_direct_urls(self) -> None:
        t1 = parse_target("https://socialblade.com/youtube/handle/vecyang")
        self.assertEqual(t1.platform, "youtube")
        self.assertEqual(t1.identifier_type, "handle")
        self.assertEqual(t1.identifier, "vecyang")
        self.assertEqual(t1.matched_rule_id, "R1_SOCIALBLADE_URL")

        t2 = parse_target("https://socialblade.com/tiktok/user/khaby.lame")
        self.assertEqual(t2.platform, "tiktok")
        self.assertEqual(t2.identifier_type, "user")
        self.assertEqual(t2.identifier, "khaby.lame")
        self.assertEqual(t2.web_path, "/tiktok/user/khaby.lame")

    def test_parse_multi_platform_urls_and_prefixes(self) -> None:
        tx = parse_target("https://x.com/HTX_Studio")
        self.assertEqual(tx.platform, "twitter")
        self.assertEqual(tx.identifier, "HTX_Studio")
        self.assertIsNone(tx.web_path)
        self.assertEqual(tx.matched_rule_id, "R3_TWITTER_URL")

        tt = parse_target("https://www.tiktok.com/@khaby.lame")
        self.assertEqual(tt.platform, "tiktok")
        self.assertEqual(tt.identifier, "khaby.lame")
        self.assertEqual(tt.web_path, "/tiktok/user/khaby.lame")

        tig = parse_target("ig:@instagram")
        self.assertEqual(tig.platform, "instagram")
        self.assertEqual(tig.identifier, "instagram")
        self.assertEqual(tig.web_path, "/instagram/user/instagram")

        ttw = parse_target("twitch:ninja")
        self.assertEqual(ttw.platform, "twitch")
        self.assertEqual(ttw.identifier, "ninja")
        self.assertEqual(ttw.web_path, "/twitch/user/ninja")

        tfb = parse_target("fb:145754375148")
        self.assertEqual(tfb.platform, "facebook")
        self.assertEqual(tfb.identifier_type, "page")
        self.assertEqual(tfb.web_path, "/facebook/page/145754375148")

    def test_adversarial_invalid_inputs_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_target("")
        with self.assertRaises(ValueError):
            parse_target("   ")
        with self.assertRaises(ValueError):
            parse_target("@vecyang", explicit_platform="myspace")
        with self.assertRaises(ValueError):
            parse_target("https://example.com/unknown")
        with self.assertRaises(ValueError):
            parse_target("https://x.com/")

    def test_tier_selection_rules(self) -> None:
        cfg_no_creds = SocialBladeConfig.resolve(cli_client_id="", cli_token="")
        # Free Matrix API sample query=socialblade
        t_sample = parse_target("youtube:socialblade")
        plan_sample = select_tier_plan(t_sample, cfg_no_creds, "auto")
        self.assertEqual(plan_sample["primary_tier"], "matrix_api")
        self.assertTrue(plan_sample["can_execute"])

        # Normal YouTube channel without paid Matrix key -> ego_trpc primary
        t_vec = parse_target("youtube:@vecyang")
        plan_vec = select_tier_plan(t_vec, cfg_no_creds, "auto")
        self.assertEqual(plan_vec["primary_tier"], "ego_trpc")
        self.assertIn("platform_bridge", plan_vec["fallback_tiers"])

        # Twitter/X -> platform_bridge in auto mode, rejected in explicit matrix/ego mode
        t_tw = parse_target("twitter:HTX_Studio")
        plan_tw_auto = select_tier_plan(t_tw, cfg_no_creds, "auto")
        self.assertEqual(plan_tw_auto["primary_tier"], "platform_bridge")
        self.assertTrue(plan_tw_auto["can_execute"])

        plan_tw_matrix = select_tier_plan(t_tw, cfg_no_creds, "matrix")
        self.assertFalse(plan_tw_matrix["can_execute"])
        self.assertEqual(plan_tw_matrix["primary_tier"], "unsupported_on_socialblade")

        # --tier web is an alias for ego_trpc
        plan_web = select_tier_plan(t_vec, cfg_no_creds, "web")
        self.assertEqual(plan_web["primary_tier"], "ego_trpc")

    def test_browser_backend_resolution(self) -> None:
        from socialblade_core.browser import resolve_backend

        cfg_pw = SocialBladeConfig.resolve(cli_browser_backend="playwright")
        with mock.patch("socialblade_core.browser.playwright_installed", return_value=True):
            b_name, b_reason = resolve_backend(cfg_pw)
            self.assertEqual(b_name, "playwright")
            self.assertIn("playwright", b_name)

        cfg_cdp = SocialBladeConfig.resolve(
            cli_browser_backend="cdp",
            cli_cdp_url="http://127.0.0.1:9222",
        )
        with mock.patch("socialblade_core.browser.playwright_installed", return_value=True):
            b_cdp, _ = resolve_backend(cfg_cdp)
            self.assertEqual(b_cdp, "cdp")


class TestLedgerSSOTAndDerivedDeltas(unittest.TestCase):
    """Verify SQLite SSOT, canonical ID deduplication, derived deltas, and anti-truncation."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_snapshots.db"
        self.ledger = SnapshotLedger(self.db_path)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_no_redundant_delta_columns_in_sqlite_schema(self) -> None:
        """SSOT Invariant: SQLite daily_snapshots must NOT store static delta columns."""
        conn = sqlite3.connect(str(self.db_path))
        cols = [
            r[1]
            for r in conn.execute("PRAGMA table_info(daily_snapshots)").fetchall()
        ]
        conn.close()
        self.assertNotIn("delta_followers", cols)
        self.assertNotIn("delta_views", cols)
        self.assertNotIn("delta_posts", cols)

    def test_canonical_id_deduplication_and_read_after_write(self) -> None:
        daily = [
            DailyPoint(date="2026-10-21", followers=868, views=320115, posts=85, source="ego_trpc"),
            DailyPoint(date="2026-10-22", followers=870, views=321246, posts=85, source="ego_trpc"),
            DailyPoint(date="2026-10-23", followers=871, views=322312, posts=85, source="ego_trpc"),
        ]
        rep = CreatorStatsReport(
            canonical_id="youtube:UC8kOCg0Cf8Kw7b_YikLlAig",
            platform="youtube",
            platform_id="UC8kOCg0Cf8Kw7b_YikLlAig",
            username="vecyang",
            display_name="Vec on Online Income",
            handle="@vecyang",
            grade="C-",
            ranks={"sb": 1202315, "followers": 4170},
            totals={"followers": 871, "views": 322312, "posts": 85},
            daily=daily,
            data_source="ego_trpc",
        )
        verified = self.ledger.upsert_report(
            rep,
            requested_days=3,
            extra_aliases=["vec_alias_old", "@vecyang"],
        )
        self.assertTrue(verified.ledger_verified)
        self.assertTrue(verified.completeness.is_complete)
        self.assertIsNone(verified.completeness.truncation_warning)

        # Verify derived deltas on read
        self.assertIsNone(verified.daily[0].delta_followers)
        self.assertEqual(verified.daily[1].delta_followers, 2)
        self.assertEqual(verified.daily[1].delta_views, 1131)
        self.assertEqual(verified.daily[2].delta_followers, 1)
        self.assertEqual(verified.daily[2].delta_views, 1066)

        # Verify all aliases resolve to the single canonical_id
        self.assertEqual(
            self.ledger.resolve_canonical_id("youtube", "vecyang"),
            "youtube:UC8kOCg0Cf8Kw7b_YikLlAig",
        )
        self.assertEqual(
            self.ledger.resolve_canonical_id("youtube", "UC8kOCg0Cf8Kw7b_YikLlAig"),
            "youtube:UC8kOCg0Cf8Kw7b_YikLlAig",
        )
        self.assertEqual(
            self.ledger.resolve_canonical_id("youtube", "vec_alias_old"),
            "youtube:UC8kOCg0Cf8Kw7b_YikLlAig",
        )
        self.assertEqual(self.ledger.stats_summary()["creators_count"], 1)

    def test_truncation_warning_when_window_incomplete(self) -> None:
        receipt = build_completeness_receipt(
            requested_days=30,
            returned_days=1,
            platform="twitter",
            data_source="tikhub_twitter+local_ledger",
        )
        self.assertFalse(receipt.is_complete)
        self.assertEqual(receipt.returned_days, 1)
        self.assertEqual(receipt.requested_days, 30)
        self.assertIn("Incomplete window: returned 1/30", receipt.truncation_warning or "")


class TestConfigAndMatrixParser(unittest.TestCase):
    """Verify config precedence, secret redaction, and Matrix API response normalization."""

    def test_config_precedence_and_secret_redaction(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "SOCIALBLADE_CLIENT_ID": "env_cid_123456789",
                "SOCIALBLADE_TOKEN": "env_tok_987654321",
                "SOCIALBLADE_DEFAULT_DAYS": "14",
            },
            clear=False,
        ):
            cfg = SocialBladeConfig.resolve(cli_days=30)
            self.assertEqual(cfg.default_days.value, 30)
            self.assertEqual(cfg.default_days.source, "cli")
            self.assertEqual(cfg.matrix_client_id.source, "env:SOCIALBLADE_CLIENT_ID")
            diag = cfg.as_diagnostic_dict()["effective_values"]
            # Secrets must be redacted in diagnostic output
            self.assertEqual(diag["matrix_client_id"]["redacted"], "env...789")
            self.assertNotIn("env_cid_123456789", json.dumps(diag))

    def test_parse_matrix_response_and_error(self) -> None:
        sample_payload = {
            "status": {"success": True, "status": 200},
            "data": {
                "id": {
                    "id": "UCl6vWwMCjufI8OPtOInHf0g",
                    "username": "socialblade",
                    "display_name": "Social Blade",
                    "handle": "@socialblade",
                },
                "general": {
                    "created_at": "2006-11-26T00:00:00.000Z",
                    "channel_type": "howto",
                    "geo": {"country_code": "US"},
                },
                "statistics": {
                    "total": {"uploads": 473, "subscribers": 359000, "views": 43190560},
                    "growth": {
                        "subs": {"1": 0, "7": 0, "30": 0, "365": -1000},
                        "vidviews": {"1": 0, "7": 503, "30": 1967, "365": 23282},
                    },
                },
                "misc": {"grade": {"grade": "C-"}},
                "ranks": {"sbrank": 1211092, "subscribers": 1982, "views": 661714},
                "daily": [
                    {"date": "2026-10-22T00:00:00.000Z", "subs": 359000, "views": 43190504},
                    {"date": "2026-10-23T00:00:00.000Z", "subs": 359000, "views": 43190560},
                ],
            },
        }
        rep = _parse_matrix_response(sample_payload, "youtube", requested_days=2)
        self.assertEqual(rep.canonical_id, "youtube:UCl6vWwMCjufI8OPtOInHf0g")
        self.assertEqual(rep.grade, "C-")
        self.assertEqual(rep.daily[-1].delta_views, 56)
        self.assertTrue(rep.completeness.is_complete)

        with self.assertRaises(ProviderError):
            _parse_matrix_response(
                {"status": {"success": False, "status": 401, "error": "missing clientid"}},
                "youtube",
                30,
            )


if __name__ == "__main__":
    unittest.main()
