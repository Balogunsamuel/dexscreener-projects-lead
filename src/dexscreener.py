"""
Dexscreener API client — discovers new token pairs and enriches metadata.

Monitoring Strategy:
  1. Poll GET /token-profiles/latest/v1 every ~30s for new token profiles.
  2. For each profile on a tracked chain, fetch pair data via
     GET /token-pairs/v1/{chainId}/{tokenAddress} to get pairCreatedAt, socials, websites.
  3. Filter tokens with pairCreatedAt < MAX_TOKEN_AGE_MINUTES.
"""

from __future__ import annotations

import asyncio
from collections import Counter, deque
import logging
from datetime import datetime, timezone
import re
from typing import Any, Optional

import httpx
from tenacity import RetryCallState, retry, stop_after_attempt, wait_exponential

from .config import Config
from .models import SocialLinks, TokenPair
from .utils import rate_limiters

logger = logging.getLogger("dexbot.dexscreener")
EVM_ADDRESS_PATTERN = re.compile(r"0x[a-fA-F0-9]{40}")
SOLANA_ADDRESS_PATTERN = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}")


def _record_retry_event(retry_state: RetryCallState) -> None:
    client = retry_state.args[0] if retry_state.args else None
    if isinstance(client, DexscreenerClient):
        client._metrics["retry_events"] += 1
    sleep_for = retry_state.next_action.sleep if retry_state.next_action else 0.0
    logger.warning(
        "Dexscreener call failed on attempt %d; retrying in %.1fs",
        retry_state.attempt_number,
        sleep_for,
    )


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
        self._metrics: Counter[str] = Counter()

    async def close(self) -> None:
        await self._client.aclose()

    def metrics_snapshot(self) -> dict[str, int]:
        return {
            "profile_calls": self._metrics["profile_calls"],
            "pair_calls": self._metrics["pair_calls"],
            "retry_events": self._metrics["retry_events"],
            "profile_failures": self._metrics["profile_failures"],
            "pair_failures": self._metrics["pair_failures"],
            "parse_failures": self._metrics["parse_failures"],
        }

    # ── Latest Token Profiles ───────────────────────────────────

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=2, max=10),
        before_sleep=_record_retry_event,
        reraise=True,
    )
    async def get_latest_token_profiles(self) -> list[dict[str, Any]]:
        """
        GET /token-profiles/latest/v1
        Returns list of recently updated token profiles.
        Each has: chainId, tokenAddress, description, links[].
        """
        async with self._profile_limiter:
            self._metrics["profile_calls"] += 1
            resp = await self._client.get("/token-profiles/latest/v1")
            resp.raise_for_status()
            data = resp.json()

        if isinstance(data, list):
            return data
        return []

    # ── Pair Details by Token Address ───────────────────────────

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=2, max=10),
        before_sleep=_record_retry_event,
        reraise=True,
    )
    async def get_pairs_by_token(
        self, chain_id: str, token_address: str
    ) -> list[dict[str, Any]]:
        """
        GET /token-pairs/v1/{chainId}/{tokenAddress}
        Returns list of pairs for a token on a specific chain.
        """
        async with self._pair_limiter:
            self._metrics["pair_calls"] += 1
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

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=2, max=10),
        before_sleep=_record_retry_event,
        reraise=True,
    )
    async def get_pair(self, chain_id: str, pair_address: str) -> Optional[dict]:
        """
        GET /latest/dex/pairs/{chainId}/{pairAddress}
        Returns pair details.
        """
        async with self._pair_limiter:
            self._metrics["pair_calls"] += 1
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
            self._metrics["profile_failures"] += 1
            logger.error("Failed to fetch token profiles: %s", e)
            return results

        logger.debug("Fetched %d token profiles", len(profiles))

        filtered_profiles: list[dict[str, Any]] = []
        for profile in profiles:
            chain_id = profile.get("chainId", "").lower()
            token_address = profile.get("tokenAddress", "")
            if chain_id not in self._config.tracked_chains:
                continue
            if not token_address:
                continue
            filtered_profiles.append(profile)

        max_profiles = max(self._config.dexscreener_max_profiles_per_poll, 1)
        if self._config.dexscreener_fair_chain_sampling:
            filtered_profiles = self._select_profiles_round_robin(
                filtered_profiles, max_profiles
            )
        else:
            filtered_profiles = filtered_profiles[:max_profiles]

        concurrency = max(self._config.dexscreener_pair_fetch_concurrency, 1)
        semaphore = asyncio.Semaphore(concurrency)
        tasks = [
            self._process_profile(profile, semaphore)
            for profile in filtered_profiles
        ]
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)

        for outcome in outcomes:
            if isinstance(outcome, Exception):
                self._metrics["pair_failures"] += 1
                logger.warning("Profile processing failed: %s", outcome)
                continue
            if outcome is not None:
                results.append(outcome)

        return results

    def _select_profiles_round_robin(
        self, profiles: list[dict[str, Any]], max_profiles: int
    ) -> list[dict[str, Any]]:
        buckets: dict[str, deque[dict[str, Any]]] = {
            chain: deque() for chain in self._config.tracked_chains
        }
        for profile in profiles:
            chain = profile.get("chainId", "").lower()
            if chain in buckets:
                buckets[chain].append(profile)

        chain_order = [chain for chain in self._config.tracked_chains if buckets[chain]]
        selected: list[dict[str, Any]] = []

        while chain_order and len(selected) < max_profiles:
            next_round: list[str] = []
            for chain in chain_order:
                bucket = buckets[chain]
                if bucket and len(selected) < max_profiles:
                    selected.append(bucket.popleft())
                if bucket:
                    next_round.append(chain)
            chain_order = next_round

        return selected

    async def _process_profile(
        self, profile: dict[str, Any], semaphore: asyncio.Semaphore
    ) -> Optional[tuple[TokenPair, SocialLinks]]:
        chain_id = profile.get("chainId", "").lower()
        token_address = profile.get("tokenAddress", "")

        async with semaphore:
            try:
                pairs = await self.get_pairs_by_token(chain_id, token_address)
            except Exception as e:
                self._metrics["pair_failures"] += 1
                logger.warning(
                    "Failed to get pairs for %s/%s: %s", chain_id, token_address, e
                )
                return None

            if not pairs:
                return None

            # Process first pair (primary)
            pair_data = pairs[0]
            token_pair = self._parse_pair(pair_data, chain_id)
            if token_pair is None:
                self._metrics["parse_failures"] += 1
                return None

            # Check freshness
            age_minutes = (
                datetime.now(timezone.utc) - token_pair.pair_created_at
            ).total_seconds() / 60
            if age_minutes > self._config.max_token_age_minutes:
                self._metrics["freshness_skipped"] += 1
                return None

            # Extract social links from both profile and pair data
            social = self._extract_socials(profile, pair_data)

            logger.debug(
                "Discovered new token: %s/%s (%s) — age %.1f min",
                chain_id,
                token_pair.token_symbol,
                token_pair.token_address[:10] + "…",
                age_minutes,
            )
            return token_pair, social

    # ── Internal Parsers ────────────────────────────────────────

    @staticmethod
    def _parse_pair(pair_data: dict, chain_id: str) -> Optional[TokenPair]:
        """Parse raw pair JSON into TokenPair model."""
        try:
            base = pair_data.get("baseToken", {})
            created_ms = pair_data.get("pairCreatedAt")
            if not created_ms:
                return None

            token_address = (base.get("address") or "").strip()
            pair_address = (pair_data.get("pairAddress") or "").strip()
            dexscreener_url = (pair_data.get("url") or "").strip()
            token_symbol = (base.get("symbol") or "").strip()

            if not token_address or not pair_address or not dexscreener_url:
                return None
            if token_symbol in {"", "???"}:
                return None
            if not DexscreenerClient._is_valid_address(chain_id, token_address):
                return None
            if not DexscreenerClient._is_valid_address(chain_id, pair_address):
                return None

            created_at = datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc)
            liquidity = pair_data.get("liquidity", {})

            return TokenPair(
                chain=chain_id,
                token_name=base.get("name", "Unknown"),
                token_symbol=token_symbol,
                token_address=token_address,
                pair_address=pair_address,
                dex_id=pair_data.get("dexId", ""),
                dexscreener_url=dexscreener_url,
                pair_created_at=created_at,
                liquidity_usd=liquidity.get("usd", 0) or 0,
                fdv=pair_data.get("fdv", 0) or 0,
            )
        except Exception as e:
            logger.warning("Failed to parse pair data: %s", e)
            return None

    @staticmethod
    def _is_valid_address(chain_id: str, address: str) -> bool:
        if chain_id in {"ethereum", "bsc", "base"}:
            return bool(EVM_ADDRESS_PATTERN.fullmatch(address))
        if chain_id == "solana":
            return bool(SOLANA_ADDRESS_PATTERN.fullmatch(address))
        return bool(address)

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
