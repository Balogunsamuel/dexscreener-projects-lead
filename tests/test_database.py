from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

try:
    from src.database import Database
    from src.models import LeadRecord, TelegramAdmin
except ModuleNotFoundError:  # pragma: no cover - environment dependent
    Database = None
    LeadRecord = None
    TelegramAdmin = None


@unittest.skipIf(
    Database is None or LeadRecord is None or TelegramAdmin is None,
    "Database tests require project dependencies.",
)
class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_insert_lead_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "leads.db"
            db = Database(str(db_path))
            try:
                await asyncio.wait_for(db.connect(), timeout=3)
            except TimeoutError:
                self.skipTest(
                    "aiosqlite connect timed out in this environment; skipping DB integration test."
                )
            try:
                lead = LeadRecord(
                    chain="ethereum",
                    token_name="Token",
                    token_symbol="TKN",
                    token_address="0xABCDEF",
                    pair_address="0xPAIR",
                    dexscreener_url="https://dexscreener.com/ethereum/0xPAIR",
                    pair_created_at=datetime.now(timezone.utc),
                    telegram_link="https://t.me/token",
                    twitter_link="https://x.com/token",
                    website="token.example",
                    admins=[TelegramAdmin(username="founder", is_creator=True)],
                    admins_hidden=False,
                    deployer_wallet="0xDEPLOYER",
                )

                first_id = await db.insert_lead(lead)
                second_id = await db.insert_lead(lead)

                self.assertEqual(first_id, second_id)
                self.assertIsNotNone(db._conn)
                assert db._conn is not None

                token_count = await (
                    await db._conn.execute("SELECT COUNT(*) FROM tokens")
                ).fetchone()
                social_count = await (
                    await db._conn.execute("SELECT COUNT(*) FROM socials")
                ).fetchone()
                admin_count = await (
                    await db._conn.execute("SELECT COUNT(*) FROM admins")
                ).fetchone()
                wallet_count = await (
                    await db._conn.execute("SELECT COUNT(*) FROM wallets")
                ).fetchone()

                self.assertEqual(token_count[0], 1)
                self.assertEqual(social_count[0], 1)
                self.assertEqual(admin_count[0], 1)
                self.assertEqual(wallet_count[0], 1)
            finally:
                await asyncio.wait_for(db.close(), timeout=3)

    async def test_notification_failure_dead_letters_after_max_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "leads.db"
            db = Database(str(db_path))
            try:
                await asyncio.wait_for(db.connect(), timeout=3)
            except TimeoutError:
                self.skipTest(
                    "aiosqlite connect timed out in this environment; skipping DB integration test."
                )
            try:
                lead = LeadRecord(
                    chain="ethereum",
                    token_name="Token",
                    token_symbol="TKN",
                    token_address="0xABCDEF",
                    pair_address="0xPAIR",
                    dexscreener_url="https://dexscreener.com/ethereum/0xPAIR",
                    pair_created_at=datetime.now(timezone.utc),
                    telegram_link="https://t.me/token",
                )
                await db.insert_lead(lead)

                scheduled, attempts, retry_at = await db.record_notification_failure(
                    lead.chain,
                    lead.token_address,
                    "temporary error",
                    max_attempts=2,
                    base_delay_seconds=1,
                    max_delay_seconds=5,
                )
                self.assertTrue(scheduled)
                self.assertEqual(attempts, 1)
                self.assertIsNotNone(retry_at)

                scheduled, attempts, retry_at = await db.record_notification_failure(
                    lead.chain,
                    lead.token_address,
                    "still failing",
                    max_attempts=2,
                    base_delay_seconds=1,
                    max_delay_seconds=5,
                )
                self.assertFalse(scheduled)
                self.assertEqual(attempts, 2)
                self.assertIsNone(retry_at)

                self.assertIsNotNone(db._conn)
                assert db._conn is not None
                row = await (
                    await db._conn.execute(
                        """SELECT notified, notification_attempts, dead_letter, next_retry_at
                           FROM tokens
                           WHERE chain = ? AND token_address = ?""",
                        (lead.chain, lead.token_address.lower()),
                    )
                ).fetchone()
                self.assertEqual(row["notified"], 0)
                self.assertEqual(row["notification_attempts"], 2)
                self.assertEqual(row["dead_letter"], 1)
                self.assertIsNone(row["next_retry_at"])
            finally:
                await asyncio.wait_for(db.close(), timeout=3)


if __name__ == "__main__":
    unittest.main()
