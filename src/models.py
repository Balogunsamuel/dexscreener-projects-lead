"""
Pydantic data models used across the application.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class TokenPair(BaseModel):
    """Core data for a newly discovered Dexscreener pair."""

    chain: str
    token_name: str
    token_symbol: str
    token_address: str
    pair_address: str
    dex_id: str = ""
    dexscreener_url: str
    pair_created_at: datetime
    liquidity_usd: float = 0.0
    fdv: float = 0.0


class SocialLinks(BaseModel):
    """Extracted social links for a token."""

    telegram_link: Optional[str] = None
    twitter_link: Optional[str] = None
    website: Optional[str] = None


class TelegramAdmin(BaseModel):
    """A Telegram group admin."""

    username: str
    is_creator: bool = False


class AdminResult(BaseModel):
    """Result of admin extraction for a Telegram group."""

    admins: list[TelegramAdmin] = Field(default_factory=list)
    admins_hidden: bool = False
    group_title: str = ""
    group_description: str = ""
    pinned_message_text: str = ""


class DeployerWallet(BaseModel):
    """Deployer wallet information."""

    address: str
    chain: str


class LeadRecord(BaseModel):
    """Full lead record combining all extracted intelligence."""

    # Token info
    chain: str
    token_name: str
    token_symbol: str
    token_address: str
    pair_address: str
    dexscreener_url: str
    pair_created_at: datetime

    # Social links
    telegram_link: Optional[str] = None
    twitter_link: Optional[str] = None
    website: Optional[str] = None

    # Admins
    admins: list[TelegramAdmin] = Field(default_factory=list)
    admins_hidden: bool = False

    # Wallet
    deployer_wallet: Optional[str] = None

    # Metadata
    discovered_at: datetime = Field(default_factory=datetime.utcnow)
    notified: bool = False
