"""
Telegram admin extraction using Telethon (user API).

Connects to a Telegram group/channel and extracts:
  - Admin usernames
  - Creator identification
  - Group description and pinned messages (for further social extraction)
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

from telethon import TelegramClient
from telethon.errors import (
    ChannelInvalidError,
    ChannelPrivateError,
    ChatAdminRequiredError,
    FloodWaitError,
    InviteHashExpiredError,
    InviteHashInvalidError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
)
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.messages import GetFullChatRequest
from telethon.tl.types import (
    ChannelParticipantCreator,
    ChannelParticipantsAdmins,
)

from .config import Config
from .models import AdminResult, TelegramAdmin

logger = logging.getLogger("dexbot.tg_admin")

TG_USERNAME_PATTERN = re.compile(r"https?://t\.me/([A-Za-z0-9_]+)")


class TelegramAdminExtractor:
    """Extracts admin information from Telegram groups/channels using Telethon."""

    def __init__(self, config: Config):
        self._config = config
        self._enabled = config.telegram_admin_enabled
        self._client: Optional[TelegramClient] = None
        self._connected = False

    async def connect(self) -> None:
        """Initialize and connect the Telethon client."""
        if not self._enabled:
            return
        if self._connected:
            return

        self._client = TelegramClient(
            "dexbot_session",
            self._config.telegram_api_id,
            self._config.telegram_api_hash,
        )

        await self._client.start(phone=self._config.telegram_phone)
        self._connected = True
        logger.info("Telethon client connected")

    async def disconnect(self) -> None:
        """Disconnect the Telethon client."""
        if self._client and self._connected:
            await self._client.disconnect()
            self._connected = False
            logger.info("Telethon client disconnected")

    def _extract_username(self, tg_link: str) -> str:
        """Extract the group/channel username from a t.me link."""
        match = TG_USERNAME_PATTERN.match(tg_link)
        if match:
            return match.group(1)
        # Fallback: assume it's already a username
        return tg_link.lstrip("@")

    async def extract_admins(self, tg_link: str) -> AdminResult:
        """
        Extract admin usernames from a Telegram group/channel.

        Returns AdminResult with:
          - admins: list of TelegramAdmin (username, is_creator)
          - admins_hidden: True if admins couldn't be fetched
          - group_description: for further social link extraction
          - pinned_message_text: for further social link extraction
        """
        if not self._enabled:
            return AdminResult(admins_hidden=True)

        if not self._client or not self._connected:
            await self.connect()

        assert self._client is not None

        username = self._extract_username(tg_link)
        result = AdminResult()

        try:
            # Resolve entity
            entity = await self._client.get_entity(username)
            logger.debug("Resolved entity: %s (type=%s)", username, type(entity).__name__)

        except (
            UsernameNotOccupiedError,
            UsernameInvalidError,
            ValueError,
        ) as e:
            logger.warning("Could not resolve Telegram entity %s: %s", username, e)
            return result

        except (ChannelPrivateError, ChannelInvalidError) as e:
            logger.warning("Channel %s is private or invalid: %s", username, e)
            return result

        except (InviteHashExpiredError, InviteHashInvalidError) as e:
            logger.warning("Invite link for %s is invalid: %s", username, e)
            return result

        except FloodWaitError as e:
            logger.error("Telegram flood wait: %d seconds", e.seconds)
            await asyncio.sleep(min(e.seconds, 60))
            return result

        except Exception as e:
            logger.error("Unexpected error resolving %s: %s", username, e)
            return result

        # ── Get full channel info for description ──
        try:
            if hasattr(entity, "megagroup") or hasattr(entity, "broadcast"):
                full = await self._client(GetFullChannelRequest(entity))
                result.group_title = getattr(entity, "title", "")
                result.group_description = getattr(full.full_chat, "about", "") or ""
            else:
                full = await self._client(GetFullChatRequest(entity.id))
                result.group_title = getattr(entity, "title", "")
                result.group_description = getattr(full.full_chat, "about", "") or ""
        except Exception as e:
            logger.debug("Could not get full info for %s: %s", username, e)

        # ── Get pinned message ──
        try:
            pinned = await self._client.get_messages(entity, ids=None, limit=1)
            if pinned:
                for msg in pinned:
                    if msg and getattr(msg, "pinned", False):
                        result.pinned_message_text = msg.text or ""
                        break

            # Alternative: iter pinned
            async for msg in self._client.iter_messages(entity, limit=5):
                if getattr(msg, "pinned", False):
                    result.pinned_message_text = msg.text or ""
                    break
        except Exception as e:
            logger.debug("Could not get pinned message for %s: %s", username, e)

        # ── Extract admins ──
        try:
            participants = await self._client.get_participants(
                entity,
                filter=ChannelParticipantsAdmins(),
                limit=100,
            )

            for participant in participants:
                user_username = getattr(participant, "username", None)
                if not user_username:
                    continue

                # Check participant type for creator
                is_creator = False
                if hasattr(participant, "participant"):
                    p = participant.participant
                    if isinstance(p, ChannelParticipantCreator):
                        is_creator = True

                result.admins.append(
                    TelegramAdmin(username=user_username, is_creator=is_creator)
                )

            logger.info(
                "Extracted %d admins from %s", len(result.admins), username
            )

        except ChatAdminRequiredError:
            logger.info("Admin list hidden for %s", username)
            result.admins_hidden = True

        except FloodWaitError as e:
            logger.error("Flood wait during admin extraction: %d sec", e.seconds)
            await asyncio.sleep(min(e.seconds, 60))
            result.admins_hidden = True

        except Exception as e:
            logger.warning("Failed to extract admins from %s: %s", username, e)
            result.admins_hidden = True

        return result
