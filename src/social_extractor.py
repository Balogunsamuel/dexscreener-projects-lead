"""
Social link validation and enrichment.
Validates Telegram links, Twitter/X links, and website URLs.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

import httpx

from .models import SocialLinks
from .utils import rate_limiters

logger = logging.getLogger("dexbot.social")

# Regex patterns
TG_LINK_PATTERN = re.compile(r"https?://t\.me/([A-Za-z0-9_]+)")
TWITTER_PATTERN = re.compile(r"https?://(?:twitter\.com|x\.com)/([A-Za-z0-9_]+)")


class SocialExtractor:
    """Validates and enriches social links."""

    def __init__(self, strict_validation: bool = False):
        self._strict_validation = strict_validation
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0),
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; DexBot/1.0)",
            },
        )
        self._limiter = rate_limiters.get("social_http", max_calls=10, period=1.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def validate_telegram_link(self, url: str) -> bool:
        """
        Check if a Telegram group/channel link is public and accessible.
        Returns True if the link resolves successfully.
        """
        match = TG_LINK_PATTERN.match(url)
        if not match:
            return False

        group_name = match.group(1)

        # Skip known non-group links
        if group_name.lower() in ("share", "addstickers", "joinchat", "proxy", "socks"):
            return False

        try:
            async with self._limiter:
                resp = await self._client.get(url)
            # Telegram returns 200 for valid public groups
            if resp.status_code == 200:
                # Check page content for group indicators
                text = resp.text.lower()
                # If the page has "join" or "members" it's likely a valid group
                if any(kw in text for kw in ["tgme_page", "members", "subscribers"]):
                    logger.debug("Telegram link validated: %s", url)
                    return True
                # Even 200 with no specific indicators — assume valid
                return True
            return False
        except Exception as e:
            logger.warning("Failed to validate Telegram link %s: %s", url, e)
            return False

    async def validate_twitter_link(self, url: str) -> bool:
        """
        Validate a Twitter/X link by checking HTTP status.
        """
        if not url:
            return False

        try:
            async with self._limiter:
                resp = await self._client.head(url)
            return resp.status_code in (200, 301, 302, 303, 307, 308)
        except Exception as e:
            logger.debug("Twitter link validation failed for %s: %s", url, e)
            # Don't discard — Twitter often blocks automated checks
            return True  # Assume valid if we got the link from Dexscreener

    async def validate_and_enrich(self, socials: SocialLinks) -> SocialLinks:
        """
        Validate all social links. Discard invalid ones.
        """
        telegram = socials.telegram_link
        twitter = socials.twitter_link
        website = socials.website

        # Validate Telegram
        if telegram and not await self.validate_telegram_link(telegram):
            logger.info("Invalid Telegram link discarded: %s", telegram)
            telegram = None

        # Validate Twitter (soft validation)
        if twitter:
            is_valid_twitter = await self.validate_twitter_link(twitter)
            if self._strict_validation and not is_valid_twitter:
                logger.info("Invalid Twitter/X link discarded in strict mode: %s", twitter)
                twitter = None

        # Normalize website to domain
        if website:
            website = self._normalize_website(website)

        return SocialLinks(
            telegram_link=telegram,
            twitter_link=twitter,
            website=website,
        )

    @staticmethod
    def _normalize_website(url: str) -> str:
        """Normalize URL to root domain."""
        try:
            if not url.startswith("http"):
                url = "https://" + url
            parsed = urlparse(url)
            domain = parsed.netloc or parsed.path
            if domain.startswith("www."):
                domain = domain[4:]
            return domain.split(":")[0].lower()
        except Exception:
            return url

    @staticmethod
    def extract_links_from_text(text: str) -> SocialLinks:
        """
        Extract Telegram and Twitter links from arbitrary text
        (e.g., group description, pinned messages).
        """
        telegram = None
        twitter = None
        website = None

        tg_match = TG_LINK_PATTERN.search(text)
        if tg_match:
            telegram = tg_match.group(0)

        tw_match = TWITTER_PATTERN.search(text)
        if tw_match:
            twitter = tw_match.group(0)

        # Generic website detection (http/https links that aren't social)
        url_pattern = re.compile(r"https?://[^\s<>\"']+")
        for match in url_pattern.finditer(text):
            url = match.group(0).rstrip(".,!)")
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            # Skip known social domains
            if any(
                s in domain
                for s in ["t.me", "twitter.com", "x.com", "telegram.org", "discord"]
            ):
                continue
            if not website:
                website = url

        return SocialLinks(
            telegram_link=telegram,
            twitter_link=twitter,
            website=website,
        )
