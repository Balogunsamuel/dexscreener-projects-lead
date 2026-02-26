"""
Telegram channel notifier using python-telegram-bot.
Sends formatted lead messages to a private Telegram channel.
"""

from __future__ import annotations

import logging
from html import escape as html_escape
from urllib.parse import urlparse

from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import RetryAfter, TelegramError

from .config import Config
from .models import LeadRecord

logger = logging.getLogger("dexbot.notifier")


class Notifier:
    """Sends formatted lead notifications to a Telegram channel."""

    def __init__(self, config: Config):
        self._config = config
        self._bot = Bot(token=config.telegram_bot_token)
        self._channel_id = config.telegram_channel_id

    async def send_lead(self, lead: LeadRecord) -> bool:
        """
        Send a formatted lead notification to the configured channel.
        Returns True if sent successfully.
        """
        message = self._format_message(lead)

        try:
            await self._bot.send_message(
                chat_id=self._channel_id,
                text=message,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            logger.info(
                "Notification sent for %s/%s",
                lead.chain.upper(),
                lead.token_symbol,
            )
            return True

        except RetryAfter as e:
            logger.warning("Rate limited by Telegram — retry after %d sec", e.retry_after)
            import asyncio
            await asyncio.sleep(e.retry_after)
            # Retry once
            try:
                await self._bot.send_message(
                    chat_id=self._channel_id,
                    text=message,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
                return True
            except Exception as retry_err:
                logger.error("Retry failed: %s", retry_err)
                return False

        except TelegramError as e:
            logger.error("Failed to send notification: %s", e)
            return False

    @staticmethod
    def _format_message(lead: LeadRecord) -> str:
        """Format the lead into a Telegram HTML message."""

        # Chain emoji mapping
        chain_emoji = {
            "ethereum": "⟠",
            "bsc": "🟡",
            "base": "🔵",
            "solana": "🟣",
        }
        emoji = chain_emoji.get(lead.chain, "🔗")

        social_lines = []
        if lead.telegram_link:
            social_lines.append(f"💬 <b>Telegram:</b> {_format_link(lead.telegram_link)}")
        if lead.twitter_link:
            social_lines.append(f"🐦 <b>Twitter:</b> {_format_link(lead.twitter_link)}")
        if lead.website:
            social_lines.append(f"🌐 <b>Website:</b> {_format_link(lead.website)}")

        social_section = ""
        if social_lines:
            social_section = "\n".join(social_lines) + "\n\n"

        message = (
            f"🚀 <b>New Dexscreener Lead Detected</b>\n"
            f"\n"
            f"{emoji} <b>Chain:</b> {_escape(lead.chain.upper())}\n"
            f"📛 <b>Name:</b> {_escape(lead.token_name)}\n"
            f"🏷 <b>Symbol:</b> ${_escape(lead.token_symbol)}\n"
            f"📋 <b>Contract:</b> <code>{_escape(lead.token_address)}</code>\n"
            f"\n"
            f"{social_section}"
            f"{_format_wallet_section(lead.deployer_wallet)}"
            f"📊 <b>Dexscreener:</b>\n"
            f"{_format_link(lead.dexscreener_url)}\n"
        )

        return message


def _escape(text: str) -> str:
    """Escape HTML special characters."""
    return html_escape(text, quote=True)


def _format_link(url: str | None) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return _escape(url)
    safe_url = _escape(url)
    return f'<a href="{safe_url}">{safe_url}</a>'


def _format_wallet_section(wallet: str | None) -> str:
    if not wallet:
        return ""
    return (
        "💳 <b>Deployer Wallet:</b>\n"
        f"<code>{_escape(wallet)}</code>\n\n"
    )
