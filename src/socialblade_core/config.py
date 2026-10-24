"""Zero-hardcoding configuration resolver for Social Blade CLI.

Enforces explicit precedence:
    CLI flag > Environment Variable > 1Password (unattended) > Default
Every effective setting records its active value and source for diagnostic
observability (`socialblade diagnose`).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_MATRIX_BASE_URL = "https://matrix.sbapis.com/b"
DEFAULT_WEB_BASE_URL = "https://socialblade.com"
DEFAULT_TIKHUB_TWITTER_BASE_URL = "https://api.tikhub.io/api/v1/twitter/web"
DEFAULT_DB_PATH = Path.home() / ".local" / "share" / "socialblade-cli" / "snapshots.db"
DEFAULT_BROWSER_PROFILE_DIR = (
    Path.home() / ".local" / "share" / "socialblade-cli" / "browser-profile"
)
DEFAULT_DAYS = 30
DEFAULT_EGO_SPACE = "socialblade-probe"
DEFAULT_TIMEOUT_MS = 12000
DEFAULT_BROWSER_BACKEND = "auto"
DEFAULT_BROWSER_CHANNEL = "chrome"
DEFAULT_BROWSER_HEADLESS = False


@dataclass(frozen=True)
class ConfigValue:
    """Tracks an effective configuration value and where it came from."""

    value: Any
    source: str  # "cli" | "env:..." | "1password" | "PATH" | "default" | "missing"
    secret: bool = False

    def redacted(self) -> Any:
        if not self.secret:
            return self.value
        val_str = str(self.value or "")
        if not val_str:
            return None
        if len(val_str) <= 6:
            return "***"
        return f"{val_str[:3]}...{val_str[-3:]}"


def _try_resolve_1password(ref: str) -> str | None:
    """Attempt unattended 1Password resolution via helper module or `op read`."""
    if not ref or not ref.startswith("op://"):
        return None

    helper_dir_raw = os.environ.get("SOCIALBLADE_OP_HELPER_DIR", "").strip()
    if helper_dir_raw:
        op_scripts = Path(helper_dir_raw).expanduser()
        if op_scripts.exists():
            try:
                if str(op_scripts) not in sys.path:
                    sys.path.insert(0, str(op_scripts))
                import op_reference  # type: ignore

                resolved = op_reference.resolve(ref)
                if resolved and not op_reference.is_reference(resolved):
                    return str(resolved).strip()
            except Exception:
                pass

    op_bin = shutil.which("op")
    if op_bin:
        try:
            proc = subprocess.run(
                [op_bin, "read", ref],
                capture_output=True,
                text=True,
                timeout=8,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip()
        except Exception:
            pass
    return None


@dataclass
class SocialBladeConfig:
    """Resolved runtime configuration with full source provenance."""

    db_path: ConfigValue
    matrix_base_url: ConfigValue
    web_base_url: ConfigValue
    matrix_client_id: ConfigValue
    matrix_token: ConfigValue
    default_days: ConfigValue
    ego_task_space: ConfigValue
    timeout_ms: ConfigValue
    browser_backend: ConfigValue
    cdp_url: ConfigValue
    browser_channel: ConfigValue
    browser_headless: ConfigValue
    browser_profile_dir: ConfigValue
    tikhub_api_key: ConfigValue
    tikhub_twitter_base_url: ConfigValue
    ego_browser_bin: ConfigValue
    tikhub_twitter_bin: ConfigValue
    yt_data_bin: ConfigValue
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def resolve(
        cls,
        *,
        cli_db_path: str | None = None,
        cli_client_id: str | None = None,
        cli_token: str | None = None,
        cli_days: int | None = None,
        cli_ego_space: str | None = None,
        cli_timeout_ms: int | None = None,
        cli_browser_backend: str | None = None,
        cli_cdp_url: str | None = None,
        cli_browser_headless: bool | None = None,
        enable_1password: bool | None = None,
    ) -> SocialBladeConfig:
        use_1password = (
            enable_1password
            if enable_1password is not None
            else os.environ.get("SOCIALBLADE_USE_1PASSWORD", "").strip().lower()
            in ("1", "true", "yes")
        )

        # 1. db_path
        if cli_db_path:
            db_val = ConfigValue(Path(cli_db_path).expanduser().resolve(), "cli")
        elif os.environ.get("SOCIALBLADE_DB_PATH", "").strip():
            db_val = ConfigValue(
                Path(os.environ["SOCIALBLADE_DB_PATH"].strip()).expanduser().resolve(),
                "env:SOCIALBLADE_DB_PATH",
            )
        else:
            db_val = ConfigValue(DEFAULT_DB_PATH, "default")

        # 2. matrix_base_url
        if os.environ.get("SOCIALBLADE_MATRIX_URL", "").strip():
            matrix_url_val = ConfigValue(
                os.environ["SOCIALBLADE_MATRIX_URL"].strip().rstrip("/"),
                "env:SOCIALBLADE_MATRIX_URL",
            )
        else:
            matrix_url_val = ConfigValue(DEFAULT_MATRIX_BASE_URL, "default")

        # 3. web_base_url
        if os.environ.get("SOCIALBLADE_WEB_URL", "").strip():
            web_url_val = ConfigValue(
                os.environ["SOCIALBLADE_WEB_URL"].strip().rstrip("/"),
                "env:SOCIALBLADE_WEB_URL",
            )
        else:
            web_url_val = ConfigValue(DEFAULT_WEB_BASE_URL, "default")

        # 4. matrix_client_id
        op_cid_ref = os.environ.get("SOCIALBLADE_OP_CLIENT_ID_REF", "").strip()
        if cli_client_id:
            cid_val = ConfigValue(cli_client_id.strip(), "cli", secret=True)
        elif os.environ.get("SOCIALBLADE_CLIENT_ID", "").strip():
            raw_cid = os.environ["SOCIALBLADE_CLIENT_ID"].strip()
            if raw_cid.startswith("op://"):
                resolved = _try_resolve_1password(raw_cid)
                cid_val = ConfigValue(resolved or "", "1password:env_ref", secret=True)
            else:
                cid_val = ConfigValue(raw_cid, "env:SOCIALBLADE_CLIENT_ID", secret=True)
        elif use_1password and op_cid_ref:
            resolved = _try_resolve_1password(op_cid_ref)
            cid_val = ConfigValue(
                resolved or "",
                "1password" if resolved else "default",
                secret=True,
            )
        else:
            cid_val = ConfigValue("", "default", secret=True)

        # 5. matrix_token
        op_tok_ref = os.environ.get("SOCIALBLADE_OP_TOKEN_REF", "").strip()
        if cli_token:
            tok_val = ConfigValue(cli_token.strip(), "cli", secret=True)
        elif os.environ.get("SOCIALBLADE_TOKEN", "").strip():
            raw_tok = os.environ["SOCIALBLADE_TOKEN"].strip()
            if raw_tok.startswith("op://"):
                resolved = _try_resolve_1password(raw_tok)
                tok_val = ConfigValue(resolved or "", "1password:env_ref", secret=True)
            else:
                tok_val = ConfigValue(raw_tok, "env:SOCIALBLADE_TOKEN", secret=True)
        elif use_1password and op_tok_ref:
            resolved = _try_resolve_1password(op_tok_ref)
            tok_val = ConfigValue(
                resolved or "",
                "1password" if resolved else "default",
                secret=True,
            )
        else:
            tok_val = ConfigValue("", "default", secret=True)

        # 6. default_days
        if cli_days is not None:
            days_val = ConfigValue(max(1, int(cli_days)), "cli")
        elif os.environ.get("SOCIALBLADE_DEFAULT_DAYS", "").strip():
            days_val = ConfigValue(
                max(1, int(os.environ["SOCIALBLADE_DEFAULT_DAYS"].strip())),
                "env:SOCIALBLADE_DEFAULT_DAYS",
            )
        else:
            days_val = ConfigValue(DEFAULT_DAYS, "default")

        # 7. ego_task_space
        if cli_ego_space:
            space_val = ConfigValue(cli_ego_space.strip(), "cli")
        elif os.environ.get("SOCIALBLADE_EGO_SPACE", "").strip():
            space_val = ConfigValue(
                os.environ["SOCIALBLADE_EGO_SPACE"].strip(),
                "env:SOCIALBLADE_EGO_SPACE",
            )
        else:
            space_val = ConfigValue(DEFAULT_EGO_SPACE, "default")

        # 8. timeout_ms
        if cli_timeout_ms is not None:
            timeout_val = ConfigValue(max(1000, int(cli_timeout_ms)), "cli")
        elif os.environ.get("SOCIALBLADE_TIMEOUT_MS", "").strip():
            timeout_val = ConfigValue(
                max(1000, int(os.environ["SOCIALBLADE_TIMEOUT_MS"].strip())),
                "env:SOCIALBLADE_TIMEOUT_MS",
            )
        else:
            timeout_val = ConfigValue(DEFAULT_TIMEOUT_MS, "default")

        # 9. browser_backend (auto | ego | cdp | playwright)
        if cli_browser_backend:
            b_backend = ConfigValue(cli_browser_backend.strip().lower(), "cli")
        elif os.environ.get("SOCIALBLADE_BROWSER_BACKEND", "").strip():
            b_backend = ConfigValue(
                os.environ["SOCIALBLADE_BROWSER_BACKEND"].strip().lower(),
                "env:SOCIALBLADE_BROWSER_BACKEND",
            )
        else:
            b_backend = ConfigValue(DEFAULT_BROWSER_BACKEND, "default")

        # 10. cdp_url
        if cli_cdp_url:
            cdp_val = ConfigValue(cli_cdp_url.strip(), "cli")
        elif os.environ.get("SOCIALBLADE_CDP_URL", "").strip():
            cdp_val = ConfigValue(
                os.environ["SOCIALBLADE_CDP_URL"].strip(),
                "env:SOCIALBLADE_CDP_URL",
            )
        else:
            cdp_val = ConfigValue("", "default")

        # 11. browser_channel & headless & profile_dir
        if os.environ.get("SOCIALBLADE_BROWSER_CHANNEL", "").strip():
            b_chan = ConfigValue(
                os.environ["SOCIALBLADE_BROWSER_CHANNEL"].strip(),
                "env:SOCIALBLADE_BROWSER_CHANNEL",
            )
        else:
            b_chan = ConfigValue(DEFAULT_BROWSER_CHANNEL, "default")

        if cli_browser_headless is not None:
            b_headless = ConfigValue(bool(cli_browser_headless), "cli")
        elif os.environ.get("SOCIALBLADE_BROWSER_HEADLESS", "").strip():
            raw_hl = os.environ["SOCIALBLADE_BROWSER_HEADLESS"].strip().lower()
            b_headless = ConfigValue(
                raw_hl in ("1", "true", "yes"),
                "env:SOCIALBLADE_BROWSER_HEADLESS",
            )
        else:
            b_headless = ConfigValue(DEFAULT_BROWSER_HEADLESS, "default")

        if os.environ.get("SOCIALBLADE_BROWSER_PROFILE_DIR", "").strip():
            b_prof = ConfigValue(
                Path(os.environ["SOCIALBLADE_BROWSER_PROFILE_DIR"].strip())
                .expanduser()
                .resolve(),
                "env:SOCIALBLADE_BROWSER_PROFILE_DIR",
            )
        else:
            b_prof = ConfigValue(DEFAULT_BROWSER_PROFILE_DIR, "default")

        # 12. TikHub API key & base URL (for direct Twitter/X bridge without extra binaries)
        raw_tikhub = os.environ.get("TIKHUB_API_KEY", "").strip()
        if raw_tikhub:
            if raw_tikhub.startswith("op://"):
                resolved_th = _try_resolve_1password(raw_tikhub)
                tikhub_key_val = ConfigValue(
                    resolved_th or "", "1password:TIKHUB_API_KEY", secret=True
                )
            else:
                tikhub_key_val = ConfigValue(
                    raw_tikhub, "env:TIKHUB_API_KEY", secret=True
                )
        else:
            tikhub_key_val = ConfigValue("", "default", secret=True)

        if os.environ.get("SOCIALBLADE_TIKHUB_TWITTER_URL", "").strip():
            tikhub_url_val = ConfigValue(
                os.environ["SOCIALBLADE_TIKHUB_TWITTER_URL"].strip().rstrip("/"),
                "env:SOCIALBLADE_TIKHUB_TWITTER_URL",
            )
        else:
            tikhub_url_val = ConfigValue(DEFAULT_TIKHUB_TWITTER_BASE_URL, "default")

        # 13. External binary paths
        ego_bin = shutil.which("ego-browser") or ""
        tikhub_bin = shutil.which("tikhub-twitter") or str(
            Path.home() / ".local" / "bin" / "tikhub-twitter"
        )
        if not Path(tikhub_bin).exists():
            tikhub_bin = ""
        yt_bin = shutil.which("yt-data") or str(
            Path.home() / ".local" / "bin" / "yt-data"
        )
        if not Path(yt_bin).exists():
            yt_bin = ""

        return cls(
            db_path=db_val,
            matrix_base_url=matrix_url_val,
            web_base_url=web_url_val,
            matrix_client_id=cid_val,
            matrix_token=tok_val,
            default_days=days_val,
            ego_task_space=space_val,
            timeout_ms=timeout_val,
            browser_backend=b_backend,
            cdp_url=cdp_val,
            browser_channel=b_chan,
            browser_headless=b_headless,
            browser_profile_dir=b_prof,
            tikhub_api_key=tikhub_key_val,
            tikhub_twitter_base_url=tikhub_url_val,
            ego_browser_bin=ConfigValue(ego_bin, "PATH" if ego_bin else "missing"),
            tikhub_twitter_bin=ConfigValue(
                tikhub_bin, "PATH" if tikhub_bin else "missing"
            ),
            yt_data_bin=ConfigValue(yt_bin, "PATH" if yt_bin else "missing"),
        )

    @property
    def has_matrix_credentials(self) -> bool:
        return bool(self.matrix_client_id.value and self.matrix_token.value)

    @property
    def has_twitter_bridge(self) -> bool:
        return bool(self.tikhub_api_key.value or self.tikhub_twitter_bin.value)

    def as_diagnostic_dict(self) -> dict[str, Any]:
        return {
            "precedence_contract": "cli > env > 1password > default",
            "effective_values": {
                "db_path": {
                    "value": str(self.db_path.value),
                    "source": self.db_path.source,
                },
                "matrix_base_url": {
                    "value": self.matrix_base_url.value,
                    "source": self.matrix_base_url.source,
                },
                "web_base_url": {
                    "value": self.web_base_url.value,
                    "source": self.web_base_url.source,
                },
                "matrix_client_id": {
                    "configured": bool(self.matrix_client_id.value),
                    "redacted": self.matrix_client_id.redacted(),
                    "source": self.matrix_client_id.source,
                },
                "matrix_token": {
                    "configured": bool(self.matrix_token.value),
                    "redacted": self.matrix_token.redacted(),
                    "source": self.matrix_token.source,
                },
                "default_days": {
                    "value": self.default_days.value,
                    "source": self.default_days.source,
                },
                "browser_backend": {
                    "value": self.browser_backend.value,
                    "source": self.browser_backend.source,
                },
                "cdp_url": {
                    "value": self.cdp_url.value or None,
                    "source": self.cdp_url.source,
                },
                "browser_channel": {
                    "value": self.browser_channel.value,
                    "source": self.browser_channel.source,
                },
                "browser_headless": {
                    "value": self.browser_headless.value,
                    "source": self.browser_headless.source,
                },
                "timeout_ms": {
                    "value": self.timeout_ms.value,
                    "source": self.timeout_ms.source,
                },
                "tikhub_api_key": {
                    "configured": bool(self.tikhub_api_key.value),
                    "redacted": self.tikhub_api_key.redacted(),
                    "source": self.tikhub_api_key.source,
                },
                "ego_browser_bin": {
                    "value": self.ego_browser_bin.value or None,
                    "source": self.ego_browser_bin.source,
                },
                "tikhub_twitter_bin": {
                    "value": self.tikhub_twitter_bin.value or None,
                    "source": self.tikhub_twitter_bin.source,
                },
                "yt_data_bin": {
                    "value": self.yt_data_bin.value or None,
                    "source": self.yt_data_bin.source,
                },
            },
        }


resolve_config = SocialBladeConfig.resolve
