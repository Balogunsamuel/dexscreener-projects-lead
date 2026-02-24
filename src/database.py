"""
SQLite persistence layer using aiosqlite.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiosqlite

from .models import LeadRecord

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
        await self._conn.commit()
        logger.info("Database connected at %s", self._db_path)

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
                pair_address, dexscreener_url, pair_created_at, discovered_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                lead.chain,
                lead.token_address.lower(),
                lead.token_name,
                lead.token_symbol,
                lead.pair_address,
                lead.dexscreener_url,
                lead.pair_created_at.isoformat(),
                lead.discovered_at.isoformat(),
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
                pair_address, dexscreener_url, pair_created_at, discovered_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
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
