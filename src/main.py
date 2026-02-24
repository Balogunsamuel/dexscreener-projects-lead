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
from collections import Counter
import logging
import signal

from .config import Config
from .database import Database
from .dexscreener import DexscreenerClient
from .models import AdminResult, LeadRecord, TokenPair
from .notifier import Notifier
from .social_extractor import SocialExtractor
from .telegram_admin import TelegramAdminExtractor
from .utils import setup_logging
from .wallet_lookup import WalletLookup

logger = logging.getLogger("dexbot.main")


class LeadBot:
    """Main bot orchestrator."""

    def __init__(self):
        self._config = Config()
        self._running = False

        # Components
        self._dex = DexscreenerClient(self._config)
        self._social = SocialExtractor(
            strict_validation=self._config.strict_social_validation
        )
        self._tg_admin = TelegramAdminExtractor(self._config)
        self._telegram_admin_runtime_enabled = self._config.telegram_admin_enabled
        self._wallet = WalletLookup(self._config)
        self._db = Database(self._config.database_path)
        self._notifier = Notifier(self._config)

        # Stats
        self._metrics: Counter[str] = Counter()
        self._service_attempts: Counter[str] = Counter()
        self._service_errors: Counter[str] = Counter()

    @property
    def _enforce_filters(self) -> bool:
        return not self._config.allow_test_leads

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
        logger.info(
            "Dex discovery: fair_chain_sampling=%s max_profiles=%d pair_concurrency=%d",
            self._config.dexscreener_fair_chain_sampling,
            self._config.dexscreener_max_profiles_per_poll,
            self._config.dexscreener_pair_fetch_concurrency,
        )
        logger.info(
            "Filters: enforce=%s require_tg=%s require_visible_admin=%s reject_hidden_admins=%s",
            self._enforce_filters,
            self._config.require_telegram_for_lead,
            self._config.require_visible_admin_for_lead,
            self._config.reject_hidden_admins,
        )
        if self._config.allow_test_leads:
            logger.warning(
                "ALLOW_TEST_LEADS=true. Non-production or low-confidence leads may be sent."
            )

        # Initialize database
        await self._db.connect()

        # Connect Telethon only when explicitly enabled and configured.
        if self._telegram_admin_runtime_enabled:
            try:
                await self._tg_admin.connect()
            except Exception as e:
                self._service_errors["telegram_admin"] += 1
                self._telegram_admin_runtime_enabled = False
                logger.warning(
                    "Telethon connection failed (admin extraction will be skipped): %s", e
                )
                logger.warning("Telegram admin extraction disabled for the current run")
        elif self._config.enable_telegram_admin_extraction:
            logger.warning(
                "Telegram admin extraction requested but credentials are incomplete; "
                "set TELEGRAM_API_ID/TELEGRAM_API_HASH/TELEGRAM_PHONE or disable "
                "ENABLE_TELEGRAM_ADMIN_EXTRACTION."
            )
        else:
            self._telegram_admin_runtime_enabled = False
            logger.info("Telegram admin extraction disabled by configuration")
            if self._enforce_filters and (
                self._config.require_visible_admin_for_lead
                or self._config.reject_hidden_admins
            ):
                logger.warning(
                    "Admin-based filters are enabled while Telegram admin extraction is disabled; "
                    "most leads may be skipped."
                )

        self._running = True

        # Handle shutdown signals
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda: asyncio.create_task(self.stop()))
            except NotImplementedError:
                # add_signal_handler is unavailable on some event loops/platforms.
                logger.debug("Signal handlers are not supported in this runtime")

        logger.info("Bot is running. Press Ctrl+C to stop.")
        logger.info("-" * 60)

        await self._poll_loop()

    async def stop(self) -> None:
        """Graceful shutdown."""
        if not self._running:
            return

        logger.info("Shutting down…")
        self._running = False

        await self._dex.close()
        await self._social.close()
        await self._tg_admin.disconnect()
        await self._wallet.close()
        await self._db.close()

        logger.info(
            "Stats: polls=%d discovered=%d processed=%d notified=%d skipped=%d",
            self._metrics["polls"],
            self._metrics["discoveries"],
            self._metrics["processed"],
            self._metrics["notified"],
            self._metrics["skipped_total"],
        )
        self._log_service_health()
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
        self._metrics["polls"] += 1
        poll_metrics: Counter[str] = Counter()
        logger.info("━━━ Poll #%d ━━━", self._metrics["polls"])

        # Step 1: Discover new tokens from Dexscreener
        self._service_attempts["dex"] += 1
        discoveries = await self._dex.discover_new_tokens()
        poll_metrics["discoveries"] += len(discoveries)
        logger.debug("Discovered %d potential leads within age window", len(discoveries))

        for token_pair, socials in discoveries:
            # Step 2: Check if already processed
            if await self._db.token_exists(token_pair.chain, token_pair.token_address):
                # Silent skip for already processed tokens to keep logs clean
                poll_metrics["skipped_already_seen"] += 1
                self._metrics["skipped_total"] += 1
                continue

            # Step 3: Validate and enrich social links
            self._service_attempts["social"] += 1
            try:
                socials = await self._social.validate_and_enrich(socials)
            except Exception as e:
                self._service_errors["social"] += 1
                logger.warning("Social validation failed for %s: %s", token_pair.token_symbol, e)
                await self._skip_token(
                    token_pair=token_pair,
                    poll_metrics=poll_metrics,
                    reason_key="skipped_social_error",
                    reason_message="social validation error",
                )
                continue

            # Filter: Must have Telegram
            if (
                self._enforce_filters
                and self._config.require_telegram_for_lead
                and not socials.telegram_link
            ):
                await self._skip_token(
                    token_pair=token_pair,
                    poll_metrics=poll_metrics,
                    reason_key="skipped_no_telegram",
                    reason_message="no Telegram link",
                )
                continue

            # Step 4: Extract Telegram admins
            admin_result = AdminResult(admins_hidden=not bool(socials.telegram_link))
            if socials.telegram_link:
                if self._telegram_admin_runtime_enabled:
                    self._service_attempts["telegram_admin"] += 1
                    try:
                        admin_result = await self._tg_admin.extract_admins(
                            socials.telegram_link
                        )
                    except Exception as e:
                        self._service_errors["telegram_admin"] += 1
                        self._telegram_admin_runtime_enabled = False
                        logger.warning(
                            "Admin extraction failed for %s/%s: %s",
                            token_pair.chain,
                            token_pair.token_symbol,
                            e,
                        )
                        logger.warning("Telegram admin extraction disabled for the current run")
                        admin_result = AdminResult(admins_hidden=True)
                else:
                    admin_result = AdminResult(admins_hidden=True)

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
            if (
                self._enforce_filters
                and self._config.require_visible_admin_for_lead
                and not admin_result.admins
                and not admin_result.admins_hidden
            ):
                await self._skip_token(
                    token_pair=token_pair,
                    poll_metrics=poll_metrics,
                    reason_key="skipped_no_visible_admins",
                    reason_message="no visible admins",
                )
                continue

            if (
                self._enforce_filters
                and self._config.reject_hidden_admins
                and admin_result.admins_hidden
                and not admin_result.admins
            ):
                await self._skip_token(
                    token_pair=token_pair,
                    poll_metrics=poll_metrics,
                    reason_key="skipped_admins_hidden",
                    reason_message="admins hidden",
                )
                continue

            # Step 5: Look up deployer wallet
            deployer_wallet = None
            if self._config.enable_wallet_lookup:
                self._service_attempts["wallet"] += 1
                try:
                    deployer_wallet = await self._wallet.get_deployer(
                        token_pair.chain, token_pair.token_address
                    )
                    poll_metrics["wallet_lookup_ok" if deployer_wallet else "wallet_lookup_miss"] += 1
                except Exception as e:
                    self._service_errors["wallet"] += 1
                    poll_metrics["wallet_lookup_error"] += 1
                    logger.warning(
                        "Wallet lookup failed for %s/%s: %s",
                        token_pair.chain,
                        token_pair.token_symbol,
                        e,
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

            # Step 7: Persist to database
            self._service_attempts["db"] += 1
            try:
                await self._db.insert_lead(lead)
            except Exception as e:
                self._service_errors["db"] += 1
                logger.error(
                    "Failed to persist lead for %s/%s: %s",
                    lead.chain,
                    lead.token_symbol,
                    e,
                )
                continue

            # Step 8: Send notification
            self._service_attempts["notifier"] += 1
            try:
                success = await self._notifier.send_lead(lead)
            except Exception as e:
                self._service_errors["notifier"] += 1
                logger.error("Notification crashed for %s/%s: %s", lead.chain, lead.token_symbol, e)
                success = False

            poll_metrics["processed"] += 1
            if success:
                poll_metrics["notified"] += 1
            else:
                poll_metrics["notify_failed"] += 1

            logger.info(
                "✅ Lead processed: %s/%s | TG admins: %d | Wallet: %s",
                lead.chain.upper(),
                lead.token_symbol,
                len(lead.admins),
                "✓" if lead.deployer_wallet else "✗",
            )

        for key, value in poll_metrics.items():
            self._metrics[key] += value

        logger.info(
            "Poll complete — discovered=%d processed=%d notified=%d skipped=%d",
            poll_metrics["discoveries"],
            poll_metrics["processed"],
            poll_metrics["notified"],
            poll_metrics["skipped_already_seen"]
            + poll_metrics["skipped_no_telegram"]
            + poll_metrics["skipped_no_visible_admins"]
            + poll_metrics["skipped_admins_hidden"]
            + poll_metrics["skipped_social_error"],
        )
        self._log_service_health()

    async def _skip_token(
        self,
        token_pair: TokenPair,
        poll_metrics: Counter[str],
        reason_key: str,
        reason_message: str,
    ) -> None:
        poll_metrics[reason_key] += 1
        self._metrics["skipped_total"] += 1
        logger.info(
            "Skipping filter: %s/%s — %s",
            token_pair.chain,
            token_pair.token_symbol,
            reason_message,
        )
        if not self._config.register_skipped_tokens:
            return
        self._service_attempts["db"] += 1
        try:
            await self._db.register_token(
                chain=token_pair.chain,
                token_address=token_pair.token_address,
                token_name=token_pair.token_name,
                token_symbol=token_pair.token_symbol,
                pair_address=token_pair.pair_address,
                dexscreener_url=token_pair.dexscreener_url,
                pair_created_at=token_pair.pair_created_at,
            )
            poll_metrics["registered_skipped"] += 1
        except Exception as e:
            self._service_errors["db"] += 1
            logger.warning(
                "Failed to register skipped token %s/%s: %s",
                token_pair.chain,
                token_pair.token_symbol,
                e,
            )

    def _log_service_health(self) -> None:
        services = sorted(set(self._service_attempts) | set(self._service_errors))
        dex_metrics = self._dex.metrics_snapshot()
        logger.info(
            "Dex metrics: profile_calls=%d pair_calls=%d retries=%d pair_failures=%d parse_failures=%d",
            dex_metrics["profile_calls"],
            dex_metrics["pair_calls"],
            dex_metrics["retry_events"],
            dex_metrics["pair_failures"],
            dex_metrics["parse_failures"],
        )
        for service in services:
            attempts = self._service_attempts[service]
            errors = self._service_errors[service]
            if attempts == 0:
                continue
            error_rate = (errors / attempts) * 100
            logger.info(
                "Service health: %s errors=%d/%d (%.1f%%)",
                service,
                errors,
                attempts,
                error_rate,
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
