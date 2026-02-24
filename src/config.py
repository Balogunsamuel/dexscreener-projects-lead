"""
Configuration module — loads and validates all environment variables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Load .env from project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


def _require(key: str) -> str:
    """Return env var or raise with a helpful message."""
    val = os.getenv(key)
    if not val:
        raise EnvironmentError(f"Missing required environment variable: {key}")
    return val


def _optional(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _optional_bool(key: str, default: bool = False) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise EnvironmentError(
        f"Invalid boolean value for {key}: {raw!r}. Use true/false."
    )


def _optional_int(key: str, default: Optional[int] = None) -> Optional[int]:
    raw = os.getenv(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise EnvironmentError(
            f"Invalid integer value for {key}: {raw!r}."
        ) from exc


def _optional_csv_tuple(key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(key)
    if raw is None or not raw.strip():
        return default
    parsed = tuple(part.strip().lower() for part in raw.split(",") if part.strip())
    if not parsed:
        return default
    return parsed


@dataclass(frozen=True)
class Config:
    """Immutable application configuration."""

    # ── Telegram Bot (python-telegram-bot) ──────────────────────
    telegram_bot_token: str = field(default_factory=lambda: _require("TELEGRAM_BOT_TOKEN"))
    telegram_channel_id: str = field(default_factory=lambda: _require("TELEGRAM_CHANNEL_ID"))

    # ── Telegram User API (Telethon) ────────────────────────────
    enable_telegram_admin_extraction: bool = field(
        default_factory=lambda: _optional_bool("ENABLE_TELEGRAM_ADMIN_EXTRACTION", True)
    )
    telegram_api_id: Optional[int] = field(
        default_factory=lambda: _optional_int("TELEGRAM_API_ID")
    )
    telegram_api_hash: str = field(default_factory=lambda: _optional("TELEGRAM_API_HASH"))
    telegram_phone: str = field(default_factory=lambda: _optional("TELEGRAM_PHONE"))

    # ── Block Explorer API Keys ─────────────────────────────────
    etherscan_api_key: str = field(default_factory=lambda: _optional("ETHERSCAN_API_KEY"))
    basescan_api_key: str = field(default_factory=lambda: _optional("BASESCAN_API_KEY"))
    bscscan_api_key: str = field(default_factory=lambda: _optional("BSCSCAN_API_KEY"))

    # ── Bot Behaviour ───────────────────────────────────────────
    poll_interval_seconds: int = field(
        default_factory=lambda: _optional_int("POLL_INTERVAL_SECONDS", 30) or 30
    )
    max_token_age_minutes: int = field(
        default_factory=lambda: _optional_int("MAX_TOKEN_AGE_MINUTES", 15) or 15
    )
    database_path: str = field(
        default_factory=lambda: _optional("DATABASE_PATH", "data/leads.db")
    )
    log_level: str = field(default_factory=lambda: _optional("LOG_LEVEL", "INFO"))
    allow_test_leads: bool = field(
        default_factory=lambda: _optional_bool("ALLOW_TEST_LEADS", False)
    )
    register_skipped_tokens: bool = field(
        default_factory=lambda: _optional_bool("REGISTER_SKIPPED_TOKENS", True)
    )
    enable_wallet_lookup: bool = field(
        default_factory=lambda: _optional_bool("ENABLE_WALLET_LOOKUP", True)
    )
    strict_social_validation: bool = field(
        default_factory=lambda: _optional_bool("STRICT_SOCIAL_VALIDATION", False)
    )
    require_telegram_for_lead: bool = field(
        default_factory=lambda: _optional_bool("REQUIRE_TELEGRAM_FOR_LEAD", True)
    )
    require_visible_admin_for_lead: bool = field(
        default_factory=lambda: _optional_bool("REQUIRE_VISIBLE_ADMIN_FOR_LEAD", True)
    )
    reject_hidden_admins: bool = field(
        default_factory=lambda: _optional_bool("REJECT_HIDDEN_ADMINS", True)
    )

    # ── Dexscreener ─────────────────────────────────────────────
    dexscreener_base_url: str = "https://api.dexscreener.com"
    tracked_chains: tuple[str, ...] = field(
        default_factory=lambda: _optional_csv_tuple(
            "TRACKED_CHAINS",
            ("ethereum", "bsc", "base", "solana"),
        )
    )
    dexscreener_pair_fetch_concurrency: int = field(
        default_factory=lambda: _optional_int("DEXSCREENER_PAIR_FETCH_CONCURRENCY", 8)
        or 8
    )
    dexscreener_max_profiles_per_poll: int = field(
        default_factory=lambda: _optional_int("DEXSCREENER_MAX_PROFILES_PER_POLL", 120)
        or 120
    )
    dexscreener_fair_chain_sampling: bool = field(
        default_factory=lambda: _optional_bool("DEXSCREENER_FAIR_CHAIN_SAMPLING", True)
    )

    # ── Solana ──────────────────────────────────────────────────
    solana_rpc_url: str = field(
        default_factory=lambda: _optional(
            "SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com"
        )
    )

    def __post_init__(self) -> None:
        if self.poll_interval_seconds <= 0:
            raise EnvironmentError("POLL_INTERVAL_SECONDS must be > 0")
        if self.max_token_age_minutes <= 0:
            raise EnvironmentError("MAX_TOKEN_AGE_MINUTES must be > 0")
        if self.dexscreener_pair_fetch_concurrency <= 0:
            raise EnvironmentError("DEXSCREENER_PAIR_FETCH_CONCURRENCY must be > 0")
        if self.dexscreener_max_profiles_per_poll <= 0:
            raise EnvironmentError("DEXSCREENER_MAX_PROFILES_PER_POLL must be > 0")
        if not self.tracked_chains:
            raise EnvironmentError("TRACKED_CHAINS must not be empty")

    @property
    def telegram_admin_credentials_present(self) -> bool:
        return bool(self.telegram_api_id and self.telegram_api_hash and self.telegram_phone)

    @property
    def telegram_admin_enabled(self) -> bool:
        return self.enable_telegram_admin_extraction and self.telegram_admin_credentials_present

    # ── Explorer base URLs mapped by chain ──────────────────────
    @property
    def explorer_configs(self) -> dict[str, dict[str, str]]:
        return {
            "ethereum": {
                "api_url": "https://api.etherscan.io/api",
                "api_key": self.etherscan_api_key,
            },
            "bsc": {
                "api_url": "https://api.bscscan.com/api",
                "api_key": self.bscscan_api_key,
            },
            "base": {
                "api_url": "https://api.basescan.org/api",
                "api_key": self.basescan_api_key,
            },
        }
