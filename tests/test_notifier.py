from __future__ import annotations

import unittest
from datetime import datetime, timezone

try:
    from src.models import LeadRecord, TelegramAdmin
    from src.notifier import Notifier
except ModuleNotFoundError:  # pragma: no cover - environment dependent
    LeadRecord = None
    TelegramAdmin = None
    Notifier = None


@unittest.skipIf(
    Notifier is None or LeadRecord is None or TelegramAdmin is None,
    "Notifier tests require project dependencies.",
)
class NotifierTests(unittest.TestCase):
    def test_format_message_escapes_html_and_avoids_unsafe_links(self) -> None:
        lead = LeadRecord(
            chain="ethereum",
            token_name="Bad <b>Token</b> & Co",
            token_symbol="T<KN>",
            token_address="0xabc<123>",
            pair_address="0xpair",
            dexscreener_url="javascript:alert(1)",
            pair_created_at=datetime.now(timezone.utc),
            telegram_link="https://t.me/example_group",
            twitter_link="https://x.com/test?x=1&y=2",
            website='https://example.com/?q="bad"',
            admins=[TelegramAdmin(username="adm<in>", is_creator=True)],
            admins_hidden=False,
            deployer_wallet="0xdef<456>",
        )

        message = Notifier._format_message(lead)

        self.assertIn("Bad &lt;b&gt;Token&lt;/b&gt; &amp; Co", message)
        self.assertIn("$T&lt;KN&gt;", message)
        self.assertNotIn("<b>Admins:</b>", message)
        self.assertNotIn("@adm&lt;in&gt;", message)
        self.assertIn("<code>0xabc&lt;123&gt;</code>", message)
        self.assertIn("javascript:alert(1)", message)
        self.assertIn(
            '<a href="https://x.com/test?x=1&amp;y=2">https://x.com/test?x=1&amp;y=2</a>',
            message,
        )
        self.assertIn(
            '<a href="https://example.com/?q=&quot;bad&quot;">'
            'https://example.com/?q=&quot;bad&quot;</a>',
            message,
        )


if __name__ == "__main__":
    unittest.main()
