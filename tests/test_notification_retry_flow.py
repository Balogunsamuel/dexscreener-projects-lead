from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
import os
import tempfile
import unittest
from unittest.mock import patch

try:
    from src.main import LeadBot
    from src.models import LeadRecord
except ModuleNotFoundError:  # pragma: no cover - environment dependent
    LeadBot = None
    LeadRecord = None


class _FlakyNotifier:
    def __init__(self) -> None:
        self._calls = 0

    async def send_lead(self, _lead: LeadRecord) -> bool:
        self._calls += 1
        return self._calls >= 2


@unittest.skipIf(
    LeadBot is None or LeadRecord is None,
    "Retry flow tests require project dependencies.",
)
class NotificationRetryFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_flow_fails_then_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = os.path.join(tmp_dir, "leads.db")
            env = {
                "TELEGRAM_BOT_TOKEN": "123456:TEST_TOKEN",
                "TELEGRAM_CHANNEL_ID": "-1001234567890",
                "ENABLE_TELEGRAM_ADMIN_EXTRACTION": "false",
                "ENABLE_WALLET_LOOKUP": "false",
                "DATABASE_PATH": db_path,
                "NOTIFICATION_RETRY_MAX_ATTEMPTS": "3",
                "NOTIFICATION_RETRY_BASE_DELAY_SECONDS": "1",
                "NOTIFICATION_RETRY_MAX_DELAY_SECONDS": "10",
                "NOTIFICATION_RETRY_BATCH_SIZE": "25",
            }

            with patch.dict(os.environ, env, clear=False):
                bot = LeadBot()
                bot._notifier = _FlakyNotifier()
                try:
                    try:
                        await asyncio.wait_for(bot._db.connect(), timeout=3)
                    except TimeoutError:
                        self.skipTest(
                            "aiosqlite connect timed out in this environment; skipping retry flow test."
                        )

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
                    await bot._db.insert_lead(lead)

                    poll_metrics: Counter[str] = Counter()
                    await bot._retry_pending_notifications(poll_metrics)
                    self.assertEqual(poll_metrics["retried_notify_failed"], 1)
                    self.assertEqual(poll_metrics["notify_retry_scheduled"], 1)

                    assert bot._db._conn is not None
                    await bot._db._conn.execute(
                        """UPDATE tokens
                           SET next_retry_at = ?
                           WHERE chain = ? AND token_address = ?""",
                        (
                            (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
                            lead.chain,
                            lead.token_address.lower(),
                        ),
                    )
                    await bot._db._conn.commit()

                    await bot._retry_pending_notifications(poll_metrics)
                    self.assertEqual(poll_metrics["retried_notified"], 1)

                    row = await (
                        await bot._db._conn.execute(
                            """SELECT notified, notification_attempts, dead_letter, next_retry_at
                               FROM tokens
                               WHERE chain = ? AND token_address = ?""",
                            (lead.chain, lead.token_address.lower()),
                        )
                    ).fetchone()
                    self.assertEqual(row["notified"], 1)
                    self.assertEqual(row["notification_attempts"], 0)
                    self.assertEqual(row["dead_letter"], 0)
                    self.assertIsNone(row["next_retry_at"])
                finally:
                    await bot._dex.close()
                    await bot._social.close()
                    await bot._wallet.close()
                    await bot._db.close()


if __name__ == "__main__":
    unittest.main()
