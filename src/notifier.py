"""
Telegram channel notifier using python-telegram-bot.
Sends formatted lead messages to a private Telegram channel.
"""

from __future__ import annotations

import logging
from typing import Optional

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

        # Admins section
        admins_text = ""
        if lead.admins:
            admin_lines = []
            for admin in lead.admins:
                creator_tag = " (creator)" if admin.is_creator else ""
                admin_lines.append(f"  • @{admin.username}{creator_tag}")
            admins_text = "\n".join(admin_lines)
        elif lead.admins_hidden:
            admins_text = "  ⚠️ Admins hidden"
        else:
            admins_text = "  ❌ No admins found"

        # Twitter
        twitter_text = (
            f'<a href="{lead.twitter_link}">{lead.twitter_link}</a>'
            if lead.twitter_link
            else "Not found"
        )

        # Website
        website_text = lead.website if lead.website else "Not found"

        # Deployer wallet
        wallet_text = lead.deployer_wallet if lead.deployer_wallet else "Not found"

        # Contract address — truncated for display
        contract_display = lead.token_address
        if len(contract_display) > 20:
            contract_display = f"{contract_display[:6]}…{contract_display[-4:]}"

        message = (
            f"🚀 <b>New Dexscreener Lead Detected</b>\n"
            f"\n"
            f"{emoji} <b>Chain:</b> {lead.chain.upper()}\n"
            f"📛 <b>Name:</b> {_escape(lead.token_name)}\n"
            f"🏷 <b>Symbol:</b> ${_escape(lead.token_symbol)}\n"
            f"📋 <b>Contract:</b> <code>{lead.token_address}</code>\n"
            f"\n"
            f"💬 <b>Telegram:</b> {lead.telegram_link}\n"
            f"👥 <b>Admins:</b>\n"
            f"{admins_text}\n"
            f"\n"
            f"🐦 <b>Twitter:</b> {twitter_text}\n"
            f"🌐 <b>Website:</b> {website_text}\n"
            f"\n"
            f"💳 <b>Deployer Wallet:</b>\n"
            f"<code>{wallet_text}</code>\n"
            f"\n"
            f"📊 <b>Dexscreener:</b>\n"
            f"{lead.dexscreener_url}\n"
        )

        return message


def _escape(text: str) -> str:
    """Escape HTML special characters."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
