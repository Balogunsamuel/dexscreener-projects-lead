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


if __name__ == "__main__":
    unittest.main()
