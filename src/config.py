"""
Configuration module — loads and validates all environment variables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

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


@dataclass(frozen=True)
class Config:
    """Immutable application configuration."""

    # ── Telegram Bot (python-telegram-bot) ──────────────────────
    telegram_bot_token: str = field(default_factory=lambda: _require("TELEGRAM_BOT_TOKEN"))
    telegram_channel_id: str = field(default_factory=lambda: _require("TELEGRAM_CHANNEL_ID"))

    # ── Telegram User API (Telethon) ────────────────────────────
    telegram_api_id: int = field(default_factory=lambda: int(_require("TELEGRAM_API_ID")))
    telegram_api_hash: str = field(default_factory=lambda: _require("TELEGRAM_API_HASH"))
    telegram_phone: str = field(default_factory=lambda: _require("TELEGRAM_PHONE"))

    # ── Block Explorer API Keys ─────────────────────────────────
    etherscan_api_key: str = field(default_factory=lambda: _optional("ETHERSCAN_API_KEY"))
    basescan_api_key: str = field(default_factory=lambda: _optional("BASESCAN_API_KEY"))
    bscscan_api_key: str = field(default_factory=lambda: _optional("BSCSCAN_API_KEY"))

    # ── Bot Behaviour ───────────────────────────────────────────
    poll_interval_seconds: int = field(
        default_factory=lambda: int(_optional("POLL_INTERVAL_SECONDS", "30"))
    )
    max_token_age_minutes: int = field(
        default_factory=lambda: int(_optional("MAX_TOKEN_AGE_MINUTES", "15"))
    )
    database_path: str = field(
        default_factory=lambda: _optional("DATABASE_PATH", "data/leads.db")
    )
    log_level: str = field(default_factory=lambda: _optional("LOG_LEVEL", "INFO"))

    # ── Dexscreener ─────────────────────────────────────────────
    dexscreener_base_url: str = "https://api.dexscreener.com"
    tracked_chains: tuple[str, ...] = ("ethereum", "bsc", "base", "solana")

    # ── Solana ──────────────────────────────────────────────────
    solana_rpc_url: str = field(
        default_factory=lambda: _optional(
            "SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com"
        )
    )

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
