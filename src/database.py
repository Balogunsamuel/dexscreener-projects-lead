"""
SQLite persistence layer using aiosqlite.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import aiosqlite

from .models import LeadRecord, TelegramAdmin

logger = logging.getLogger("dexbot.database")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tokens (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    chain           TEXT    NOT NULL,
    token_address   TEXT    NOT NULL,
    token_name      TEXT    NOT NULL,
    token_symbol    TEXT    NOT NULL,
    pair_address    TEXT    NOT NULL,
    dexscreener_url TEXT    NOT NULL,
    pair_created_at TEXT    NOT NULL,
    discovered_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    notified        INTEGER NOT NULL DEFAULT 0,
    notification_attempts INTEGER NOT NULL DEFAULT 0,
    last_notify_error TEXT,
    next_retry_at   TEXT,
    dead_letter     INTEGER NOT NULL DEFAULT 0,
    UNIQUE(chain, token_address)
);

CREATE TABLE IF NOT EXISTS socials (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id    INTEGER NOT NULL REFERENCES tokens(id) ON DELETE CASCADE,
    telegram    TEXT,
    twitter     TEXT,
    website     TEXT,
    UNIQUE(token_id)
);

CREATE TABLE IF NOT EXISTS admins (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id    INTEGER NOT NULL REFERENCES tokens(id) ON DELETE CASCADE,
    username    TEXT    NOT NULL,
    is_creator  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS wallets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id        INTEGER NOT NULL REFERENCES tokens(id) ON DELETE CASCADE,
    deployer_wallet TEXT    NOT NULL,
    UNIQUE(token_id)
);

CREATE INDEX IF NOT EXISTS idx_tokens_chain_address ON tokens(chain, token_address);
CREATE INDEX IF NOT EXISTS idx_tokens_created ON tokens(pair_created_at);
"""


class Database:
    """Async SQLite database manager."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        """Open connection and initialize schema."""
        # Ensure parent directory exists
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.executescript(SCHEMA_SQL)
        await self._ensure_schema_compatibility()
        await self._conn.commit()
        logger.info("Database connected at %s", self._db_path)

    async def _ensure_schema_compatibility(self) -> None:
        assert self._conn
        cursor = await self._conn.execute("PRAGMA table_info(tokens)")
        rows = await cursor.fetchall()
        columns = {row["name"] for row in rows}
        upgraded_legacy_notify_model = False
        if "notified" not in columns:
            await self._conn.execute(
                "ALTER TABLE tokens ADD COLUMN notified INTEGER NOT NULL DEFAULT 0"
            )
            logger.info("Applied DB migration: added tokens.notified")
            upgraded_legacy_notify_model = True
        if "notification_attempts" not in columns:
            await self._conn.execute(
                "ALTER TABLE tokens ADD COLUMN notification_attempts INTEGER NOT NULL DEFAULT 0"
            )
            upgraded_legacy_notify_model = True
        if "last_notify_error" not in columns:
            await self._conn.execute(
                "ALTER TABLE tokens ADD COLUMN last_notify_error TEXT"
            )
            upgraded_legacy_notify_model = True
        if "next_retry_at" not in columns:
            await self._conn.execute(
                "ALTER TABLE tokens ADD COLUMN next_retry_at TEXT"
            )
            upgraded_legacy_notify_model = True
        if "dead_letter" not in columns:
            await self._conn.execute(
                "ALTER TABLE tokens ADD COLUMN dead_letter INTEGER NOT NULL DEFAULT 0"
            )
            upgraded_legacy_notify_model = True
        if upgraded_legacy_notify_model:
            # Avoid replaying historical rows after migration to retry queue semantics.
            await self._conn.execute(
                """UPDATE tokens
                   SET notified = 1,
                       notification_attempts = 0,
                       last_notify_error = NULL,
                       next_retry_at = NULL,
                       dead_letter = 0
                   WHERE notified = 0"""
            )
            logger.info(
                "Applied DB migration: marked existing unnotified rows as notified"
            )

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            logger.info("Database connection closed")

    async def token_exists(self, chain: str, token_address: str) -> bool:
        """Check if a token has already been stored."""
        assert self._conn
        cursor = await self._conn.execute(
            "SELECT 1 FROM tokens WHERE chain = ? AND token_address = ?",
            (chain, token_address.lower()),
        )
        row = await cursor.fetchone()
        return row is not None

    async def insert_lead(self, lead: LeadRecord) -> int:
        """
        Insert a full lead record across all tables.
        Returns the token_id.
        """
        assert self._conn

        # Insert token
        cursor = await self._conn.execute(
            """INSERT OR IGNORE INTO tokens
               (chain, token_address, token_name, token_symbol,
                pair_address, dexscreener_url, pair_created_at, discovered_at, notified,
                notification_attempts, last_notify_error, next_retry_at, dead_letter)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, NULL, 0)""",
            (
                lead.chain,
                lead.token_address.lower(),
                lead.token_name,
                lead.token_symbol,
                lead.pair_address,
                lead.dexscreener_url,
                lead.pair_created_at.isoformat(),
                lead.discovered_at.isoformat(),
                int(lead.notified),
            ),
        )

        if cursor.rowcount == 0:
            # Already existed
            cur2 = await self._conn.execute(
                "SELECT id FROM tokens WHERE chain = ? AND token_address = ?",
                (lead.chain, lead.token_address.lower()),
            )
            row = await cur2.fetchone()
            return row[0]

        token_id = cursor.lastrowid
        assert token_id is not None

        # Insert socials
        await self._conn.execute(
            """INSERT OR IGNORE INTO socials (token_id, telegram, twitter, website)
               VALUES (?, ?, ?, ?)""",
            (token_id, lead.telegram_link, lead.twitter_link, lead.website),
        )

        # Insert admins
        for admin in lead.admins:
            await self._conn.execute(
                "INSERT INTO admins (token_id, username, is_creator) VALUES (?, ?, ?)",
                (token_id, admin.username, int(admin.is_creator)),
            )

        # Insert wallet
        if lead.deployer_wallet:
            await self._conn.execute(
                "INSERT OR IGNORE INTO wallets (token_id, deployer_wallet) VALUES (?, ?)",
                (token_id, lead.deployer_wallet),
            )

        await self._conn.commit()
        logger.info(
            "Stored lead: %s/%s (token_id=%d)", lead.chain, lead.token_symbol, token_id
        )
        return token_id

    async def register_token(self, chain: str, token_address: str, token_name: str, 
                             token_symbol: str, pair_address: str, 
                             dexscreener_url: str, pair_created_at: datetime) -> None:
        """
        Register a token as 'seen' even if it's not a valid lead.
        This prevents re-processing tokens that were skipped (e.g. no Telegram).
        """
        assert self._conn
        await self._conn.execute(
            """INSERT OR IGNORE INTO tokens
               (chain, token_address, token_name, token_symbol,
                pair_address, dexscreener_url, pair_created_at, discovered_at, notified,
                notification_attempts, last_notify_error, next_retry_at, dead_letter)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, NULL, NULL, 0)""",
            (
                chain,
                token_address.lower(),
                token_name,
                token_symbol,
                pair_address,
                dexscreener_url,
                pair_created_at.isoformat(),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await self._conn.commit()

    async def mark_notified(self, chain: str, token_address: str) -> None:
        assert self._conn
        await self._conn.execute(
            """UPDATE tokens
               SET notified = 1,
                   notification_attempts = 0,
                   last_notify_error = NULL,
                   next_retry_at = NULL,
                   dead_letter = 0
               WHERE chain = ? AND token_address = ?""",
            (chain, token_address.lower()),
        )
        await self._conn.commit()

    async def record_notification_failure(
        self,
        chain: str,
        token_address: str,
        error: str,
        *,
        max_attempts: int,
        base_delay_seconds: int,
        max_delay_seconds: int,
    ) -> tuple[bool, int, Optional[datetime]]:
        """
        Register a failed notification attempt.
        Returns:
          (scheduled_for_retry, attempts, next_retry_at)
        """
        assert self._conn
        row_cursor = await self._conn.execute(
            """SELECT notification_attempts
               FROM tokens
               WHERE chain = ? AND token_address = ?""",
            (chain, token_address.lower()),
        )
        row = await row_cursor.fetchone()
        current_attempts = int(row["notification_attempts"]) if row else 0
        attempts = current_attempts + 1
        safe_error = error[:500]

        if attempts >= max_attempts:
            await self._conn.execute(
                """UPDATE tokens
                   SET notified = 0,
                       notification_attempts = ?,
                       last_notify_error = ?,
                       next_retry_at = NULL,
                       dead_letter = 1
                   WHERE chain = ? AND token_address = ?""",
                (attempts, safe_error, chain, token_address.lower()),
            )
            await self._conn.commit()
            return False, attempts, None

        delay_seconds = min(
            base_delay_seconds * (2 ** max(attempts - 1, 0)),
            max_delay_seconds,
        )
        next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
        await self._conn.execute(
            """UPDATE tokens
               SET notified = 0,
                   notification_attempts = ?,
                   last_notify_error = ?,
                   next_retry_at = ?,
                   dead_letter = 0
               WHERE chain = ? AND token_address = ?""",
            (
                attempts,
                safe_error,
                next_retry_at.isoformat(),
                chain,
                token_address.lower(),
            ),
        )
        await self._conn.commit()
        return True, attempts, next_retry_at

    async def get_unnotified_leads(self, limit: int = 25) -> list[LeadRecord]:
        assert self._conn
        now_iso = datetime.now(timezone.utc).isoformat()
        cursor = await self._conn.execute(
            """SELECT
                   t.id,
                   t.chain,
                   t.token_name,
                   t.token_symbol,
                   t.token_address,
                   t.pair_address,
                   t.dexscreener_url,
                   t.pair_created_at,
                   t.discovered_at,
                   s.telegram,
                   s.twitter,
                   s.website,
                   w.deployer_wallet
               FROM tokens t
               LEFT JOIN socials s ON s.token_id = t.id
               LEFT JOIN wallets w ON w.token_id = t.id
               WHERE t.notified = 0
                 AND t.dead_letter = 0
                 AND s.token_id IS NOT NULL
                 AND (t.next_retry_at IS NULL OR t.next_retry_at <= ?)
               ORDER BY t.discovered_at ASC
               LIMIT ?""",
            (now_iso, limit),
        )
        rows = await cursor.fetchall()

        leads: list[LeadRecord] = []
        for row in rows:
            token_id = row["id"]
            admin_cursor = await self._conn.execute(
                "SELECT username, is_creator FROM admins WHERE token_id = ? ORDER BY id ASC",
                (token_id,),
            )
            admin_rows = await admin_cursor.fetchall()
            admins = [
                TelegramAdmin(username=admin_row["username"], is_creator=bool(admin_row["is_creator"]))
                for admin_row in admin_rows
            ]

            leads.append(
                LeadRecord(
                    chain=row["chain"],
                    token_name=row["token_name"],
                    token_symbol=row["token_symbol"],
                    token_address=row["token_address"],
                    pair_address=row["pair_address"],
                    dexscreener_url=row["dexscreener_url"],
                    pair_created_at=_parse_iso_datetime(row["pair_created_at"]),
                    telegram_link=row["telegram"],
                    twitter_link=row["twitter"],
                    website=row["website"],
                    admins=admins,
                    admins_hidden=False,
                    deployer_wallet=row["deployer_wallet"],
                    discovered_at=_parse_iso_datetime(row["discovered_at"]),
                    notified=False,
                )
            )

        return leads

    async def get_recent_leads(self, limit: int = 50) -> list[dict]:
        """Retrieve recent leads for diagnostics."""
        assert self._conn
        cursor = await self._conn.execute(
            """SELECT t.*, s.telegram, s.twitter, s.website, w.deployer_wallet
               FROM tokens t
               LEFT JOIN socials s ON s.token_id = t.id
               LEFT JOIN wallets w ON w.token_id = t.id
               ORDER BY t.discovered_at DESC
               LIMIT ?""",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


def _parse_iso_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed
