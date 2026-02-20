"""
Dexscreener API client — discovers new token pairs and enriches metadata.

Monitoring Strategy:
  1. Poll GET /token-profiles/latest/v1 every ~30s for new token profiles.
  2. For each profile on a tracked chain, fetch pair data via
     GET /token-pairs/v1/{chainId}/{tokenAddress} to get pairCreatedAt, socials, websites.
  3. Filter tokens with pairCreatedAt < MAX_TOKEN_AGE_MINUTES.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import Config
from .models import SocialLinks, TokenPair
from .utils import rate_limiters

logger = logging.getLogger("dexbot.dexscreener")


class DexscreenerClient:
    """Async client for Dexscreener public API."""

    def __init__(self, config: Config):
        self._config = config
        self._base = config.dexscreener_base_url
        self._client = httpx.AsyncClient(
            base_url=self._base,
            timeout=httpx.Timeout(15.0),
            headers={"Accept": "application/json"},
        )
        # Rate limiters
        self._profile_limiter = rate_limiters.get("dex_profiles", max_calls=55, period=60)
        self._pair_limiter = rate_limiters.get("dex_pairs", max_calls=250, period=60)

    async def close(self) -> None:
        await self._client.aclose()

    # ── Latest Token Profiles ───────────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    async def get_latest_token_profiles(self) -> list[dict[str, Any]]:
        """
        GET /token-profiles/latest/v1
        Returns list of recently updated token profiles.
        Each has: chainId, tokenAddress, description, links[].
        """
        async with self._profile_limiter:
            resp = await self._client.get("/token-profiles/latest/v1")
            resp.raise_for_status()
            data = resp.json()

        if isinstance(data, list):
            return data
        return []

    # ── Pair Details by Token Address ───────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    async def get_pairs_by_token(
        self, chain_id: str, token_address: str
    ) -> list[dict[str, Any]]:
        """
        GET /token-pairs/v1/{chainId}/{tokenAddress}
        Returns list of pairs for a token on a specific chain.
        """
        async with self._pair_limiter:
            resp = await self._client.get(
                f"/token-pairs/v1/{chain_id}/{token_address}"
            )
            resp.raise_for_status()
            data = resp.json()

        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "pairs" in data:
            return data["pairs"] or []
        return []

    # ── Pair Details by Pair Address ────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    async def get_pair(self, chain_id: str, pair_address: str) -> Optional[dict]:
        """
        GET /latest/dex/pairs/{chainId}/{pairAddress}
        Returns pair details.
        """
        async with self._pair_limiter:
            resp = await self._client.get(
                f"/latest/dex/pairs/{chain_id}/{pair_address}"
            )
            resp.raise_for_status()
            data = resp.json()

        pairs = data.get("pairs") or []
        return pairs[0] if pairs else None

    # ── High-Level: Discover New Tokens ─────────────────────────

    async def discover_new_tokens(self) -> list[tuple[TokenPair, SocialLinks]]:
        """
        Poll latest profiles, filter by tracked chains, enrich with pair data,
        and return (TokenPair, SocialLinks) tuples for tokens within age window.
        """
        results: list[tuple[TokenPair, SocialLinks]] = []

        try:
            profiles = await self.get_latest_token_profiles()
        except Exception as e:
            logger.error("Failed to fetch token profiles: %s", e)
            return results

        logger.debug("Fetched %d token profiles", len(profiles))

        for profile in profiles:
            chain_id = profile.get("chainId", "").lower()
            token_address = profile.get("tokenAddress", "")

            if chain_id not in self._config.tracked_chains:
                continue

            if not token_address:
                continue

            try:
                pairs = await self.get_pairs_by_token(chain_id, token_address)
            except Exception as e:
                logger.warning(
                    "Failed to get pairs for %s/%s: %s", chain_id, token_address, e
                )
                continue

            if not pairs:
                continue

            # Process first pair (primary)
            pair_data = pairs[0]
            token_pair = self._parse_pair(pair_data, chain_id)
            if token_pair is None:
                continue

            # Check freshness
            age_minutes = (
                datetime.now(timezone.utc) - token_pair.pair_created_at
            ).total_seconds() / 60
            if age_minutes > self._config.max_token_age_minutes:
                continue

            # Extract social links from both profile and pair data
            social = self._extract_socials(profile, pair_data)

            logger.debug(
                "Discovered new token: %s/%s (%s) — age %.1f min",
                chain_id,
                token_pair.token_symbol,
                token_pair.token_address[:10] + "…",
                age_minutes,
            )
            results.append((token_pair, social))

        return results

    # ── Internal Parsers ────────────────────────────────────────

    @staticmethod
    def _parse_pair(pair_data: dict, chain_id: str) -> Optional[TokenPair]:
        """Parse raw pair JSON into TokenPair model."""
        try:
            base = pair_data.get("baseToken", {})
            created_ms = pair_data.get("pairCreatedAt")
            if not created_ms:
                return None

            created_at = datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc)
            liquidity = pair_data.get("liquidity", {})

            return TokenPair(
                chain=chain_id,
                token_name=base.get("name", "Unknown"),
                token_symbol=base.get("symbol", "???"),
                token_address=base.get("address", ""),
                pair_address=pair_data.get("pairAddress", ""),
                dex_id=pair_data.get("dexId", ""),
                dexscreener_url=pair_data.get("url", ""),
                pair_created_at=created_at,
                liquidity_usd=liquidity.get("usd", 0) or 0,
                fdv=pair_data.get("fdv", 0) or 0,
            )
        except Exception as e:
            logger.warning("Failed to parse pair data: %s", e)
            return None

    @staticmethod
    def _extract_socials(profile: dict, pair_data: dict) -> SocialLinks:
        """
        Extract Telegram, Twitter, and Website links from both
        the token profile and pair data.
        """
        telegram = None
        twitter = None
        website = None

        # ── From profile links ──
        for link in profile.get("links", []):
            link_type = (link.get("type") or link.get("label", "")).lower()
            url = link.get("url", "")
            if not url:
                continue

            if "telegram" in link_type or "t.me" in url:
                telegram = telegram or url
            elif "twitter" in link_type or "x.com" in url or "twitter.com" in url:
                twitter = twitter or url
            elif "website" in link_type:
                website = website or url

        # ── From pair info.socials ──
        info = pair_data.get("info", {})
        for social in info.get("socials", []):
            # Check for platform/handle (older format?) or type/url (newer format)
            platform = (social.get("platform") or social.get("type") or "").lower()
            handle = social.get("handle", "")
            url = social.get("url", "")

            if not platform:
                continue

            if platform == "telegram":
                if not telegram:
                    if url:
                        telegram = url
                    elif handle:
                        telegram = f"https://t.me/{handle}" if not handle.startswith("http") else handle
            elif platform in ("twitter", "x"):
                if not twitter:
                    if url:
                        twitter = url
                    elif handle:
                        twitter = f"https://x.com/{handle}" if not handle.startswith("http") else handle

        # ── From pair info.websites ──
        for w in info.get("websites", []):
            url = w.get("url", "") # Dexscreener sometimes uses label/url here too
            if not url: 
                 # Fallback if structure is different
                 url = w.get("value", "")
            
            if url and not website:
                website = url

        # ── From profile description (regex fallback) ──
        import re

        desc = profile.get("description", "") or ""
        if not telegram:
            tg_match = re.search(r"https?://t\.me/\S+", desc)
            if tg_match:
                telegram = tg_match.group(0).rstrip(".,!)")

        if not twitter:
            tw_match = re.search(r"https?://(?:twitter\.com|x\.com)/\S+", desc)
            if tw_match:
                twitter = tw_match.group(0).rstrip(".,!)")

        return SocialLinks(
            telegram_link=telegram,
            twitter_link=twitter,
            website=website,
        )
