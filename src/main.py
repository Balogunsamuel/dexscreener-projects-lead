"""
Main orchestrator — entry point for the Dexscreener Lead Bot.

Runs an asyncio loop that:
  1. Polls Dexscreener for new token profiles every POLL_INTERVAL_SECONDS
  2. Filters for tracked chains (Ethereum, BSC, Base) and freshness
  3. Validates Telegram links and extracts admin usernames
  4. Looks up deployer wallets via block explorer APIs
  5. Stores lead data in SQLite
  6. Sends formatted notifications to a Telegram channel

Usage:
    python -m src.main
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from datetime import datetime, timezone

from .config import Config
from .database import Database
from .dexscreener import DexscreenerClient
from .models import LeadRecord
from .notifier import Notifier
from .social_extractor import SocialExtractor
from .telegram_admin import TelegramAdminExtractor
from .utils import setup_logging
from .wallet_lookup import WalletLookup

logger: logging.Logger


class LeadBot:
    """Main bot orchestrator."""

    def __init__(self):
        self._config = Config()
        self._running = False

        # Components
        self._dex = DexscreenerClient(self._config)
        self._social = SocialExtractor()
        self._tg_admin = TelegramAdminExtractor(self._config)
        self._wallet = WalletLookup(self._config)
        self._db = Database(self._config.database_path)
        self._notifier = Notifier(self._config)

        # Stats
        self._poll_count = 0
        self._leads_found = 0
        self._leads_notified = 0

    async def start(self) -> None:
        """Initialize all components and start the polling loop."""
        global logger
        logger = setup_logging(self._config.log_level)

        logger.info("=" * 60)
        logger.info("🚀 Dexscreener Lead Bot starting…")
        logger.info("=" * 60)
        logger.info("Tracked chains: %s", ", ".join(self._config.tracked_chains))
        logger.info("Poll interval: %ds", self._config.poll_interval_seconds)
        logger.info("Max token age: %d min", self._config.max_token_age_minutes)
        logger.info("Database: %s", self._config.database_path)

        # Initialize database
        await self._db.connect()

        # Connect Telethon (will prompt for auth on first run)
        try:
            await self._tg_admin.connect()
        except Exception as e:
            logger.warning(
                "Telethon connection failed (admin extraction will be skipped): %s", e
            )

        self._running = True

        # Handle shutdown signals
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.stop()))

        logger.info("Bot is running. Press Ctrl+C to stop.")
        logger.info("-" * 60)

        await self._poll_loop()

    async def stop(self) -> None:
        """Graceful shutdown."""
        logger.info("Shutting down…")
        self._running = False

        await self._dex.close()
        await self._social.close()
        await self._tg_admin.disconnect()
        await self._wallet.close()
        await self._db.close()

        logger.info(
            "Stats: polls=%d, leads_found=%d, notified=%d",
            self._poll_count,
            self._leads_found,
            self._leads_notified,
        )
        logger.info("Goodbye! 👋")

    async def _poll_loop(self) -> None:
        """Main polling loop."""
        while self._running:
            try:
                await self._poll_once()
            except Exception as e:
                logger.error("Unhandled error in poll cycle: %s", e, exc_info=True)

            if self._running:
                await asyncio.sleep(self._config.poll_interval_seconds)

    async def _poll_once(self) -> None:
        """Single poll cycle: discover → enrich → filter → store → notify."""
        self._poll_count += 1
        logger.info("━━━ Poll #%d ━━━", self._poll_count)

        # Step 1: Discover new tokens from Dexscreener
        discoveries = await self._dex.discover_new_tokens()
        logger.debug("Discovered %d potential leads within age window", len(discoveries))

        new_leads_processed = 0

        for token_pair, socials in discoveries:
            # Step 2: Check if already processed
            if await self._db.token_exists(token_pair.chain, token_pair.token_address):
                # Silent skip for already processed tokens to keep logs clean
                continue

            # Step 3: Validate and enrich social links
            socials = await self._social.validate_and_enrich(socials)

            # Filter: Must have Telegram
            if not socials.telegram_link:
                logger.info(
                    "Skipping filter: %s/%s — no Telegram link (allowing for test)",
                    token_pair.chain,
                    token_pair.token_symbol,
                )
                # await self._db.register_token(...)
                # continue

            # Step 4: Extract Telegram admins
            if socials.telegram_link:
                admin_result = await self._tg_admin.extract_admins(socials.telegram_link)
            else:
                from .models import AdminResult
                admin_result = AdminResult(admins_hidden=True) # Treat as hidden/empty

            # Enrich socials from Telegram group data (description, pinned message)
            if admin_result.group_description or admin_result.pinned_message_text:
                extra_text = (
                    admin_result.group_description
                    + "\n"
                    + admin_result.pinned_message_text
                )
                extra_socials = SocialExtractor.extract_links_from_text(extra_text)

                # Fill in missing links
                if not socials.twitter_link and extra_socials.twitter_link:
                    socials.twitter_link = extra_socials.twitter_link
                    logger.info(
                        "Found Twitter from TG group: %s", socials.twitter_link
                    )
                if not socials.website and extra_socials.website:
                    socials.website = extra_socials.website
                    logger.info("Found website from TG group: %s", socials.website)

            # Filter: Must have at least one visible admin
            if not admin_result.admins and not admin_result.admins_hidden:
                logger.info(
                    "Skipping filter: %s/%s — no visible admins (allowing for test)",
                    token_pair.chain,
                    token_pair.token_symbol,
                )
                # await self._db.register_token(...)
                # continue

            # For MVP: skip if admins are hidden with no admins found
            if admin_result.admins_hidden and not admin_result.admins:
                logger.info(
                    "Skipping filter: %s/%s — admins hidden (allowing for test)",
                    token_pair.chain,
                    token_pair.token_symbol,
                )
                # await self._db.register_token(...)
                # continue

            # Step 5: Look up deployer wallet
            deployer_wallet = await self._wallet.get_deployer(
                token_pair.chain, token_pair.token_address
            )

            # Step 6: Build full lead record
            lead = LeadRecord(
                chain=token_pair.chain,
                token_name=token_pair.token_name,
                token_symbol=token_pair.token_symbol,
                token_address=token_pair.token_address,
                pair_address=token_pair.pair_address,
                dexscreener_url=token_pair.dexscreener_url,
                pair_created_at=token_pair.pair_created_at,
                telegram_link=socials.telegram_link,
                twitter_link=socials.twitter_link,
                website=socials.website,
                admins=admin_result.admins,
                admins_hidden=admin_result.admins_hidden,
                deployer_wallet=deployer_wallet,
            )

            self._leads_found += 1

            # Step 7: Persist to database
            await self._db.insert_lead(lead)

            # Step 8: Send notification
            success = await self._notifier.send_lead(lead)
            if success:
                self._leads_notified += 1

            logger.info(
                "✅ Lead processed: %s/%s | TG admins: %d | Wallet: %s",
                lead.chain.upper(),
                lead.token_symbol,
                len(lead.admins),
                "✓" if lead.deployer_wallet else "✗",
            )

        logger.info(
            "Poll complete — Total leads: %d, Notified: %d",
            self._leads_found,
            self._leads_notified,
        )


async def main() -> None:
    """Entry point."""
    bot = LeadBot()
    try:
        await bot.start()
    except KeyboardInterrupt:
        pass
    finally:
        await bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
