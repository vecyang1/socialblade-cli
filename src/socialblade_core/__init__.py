"""Social Blade & Cross-Platform Creator Growth Analytics Core (`socialblade`)."""

__version__ = "1.1.0"

from .browser import BrowserError, backend_availability, evaluate_on_socialblade, resolve_backend
from .config import ConfigValue, SocialBladeConfig
from .ledger import (
    SnapshotLedger,
    build_completeness_receipt,
    derive_daily_deltas,
    enrich_growth_from_daily,
)
from .providers import (
    ProviderError,
    execute_stats_flow,
    fetch_ego_favorites,
    fetch_ego_trpc,
    fetch_matrix_api,
    fetch_platform_bridge,
)
from .router import (
    SUPPORTED_PLATFORMS,
    build_socialblade_web_path,
    normalize_platform,
    parse_target,
    select_tier_plan,
)
from .types import (
    CompletenessReceipt,
    CreatorStatsReport,
    DailyPoint,
    GrowthSummary,
    MonthlyPoint,
    ParsedTarget,
)

__all__ = [
    "CompletenessReceipt",
    "ConfigValue",
    "CreatorStatsReport",
    "DailyPoint",
    "GrowthSummary",
    "MonthlyPoint",
    "ParsedTarget",
    "ProviderError",
    "SUPPORTED_PLATFORMS",
    "SnapshotLedger",
    "SocialBladeConfig",
    "build_completeness_receipt",
    "build_socialblade_web_path",
    "derive_daily_deltas",
    "enrich_growth_from_daily",
    "execute_stats_flow",
    "fetch_ego_favorites",
    "fetch_ego_trpc",
    "fetch_matrix_api",
    "fetch_platform_bridge",
    "normalize_platform",
    "parse_target",
    "select_tier_plan",
]
