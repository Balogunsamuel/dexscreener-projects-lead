from __future__ import annotations

import unittest
from datetime import datetime, timezone

try:
    from src.dexscreener import DexscreenerClient
except ModuleNotFoundError:  # pragma: no cover - environment dependent
    DexscreenerClient = None


@unittest.skipIf(
    DexscreenerClient is None,
    "Dexscreener tests require project dependencies (httpx/pydantic/tenacity).",
)
class DexscreenerParserTests(unittest.TestCase):
    def test_parse_pair_valid(self) -> None:
        pair_data = {
            "baseToken": {
                "name": "TokenName",
                "symbol": "TKN",
                "address": "0x1111111111111111111111111111111111111111",
            },
            "pairAddress": "0x2222222222222222222222222222222222222222",
            "pairCreatedAt": 1700000000000,
            "dexId": "uni",
            "url": "https://dexscreener.com/ethereum/0x2222222222222222222222222222222222222222",
            "liquidity": {"usd": 12345},
            "fdv": 777,
        }

        pair = DexscreenerClient._parse_pair(pair_data, "ethereum")

        self.assertIsNotNone(pair)
        assert pair is not None
        self.assertEqual(pair.chain, "ethereum")
        self.assertEqual(pair.token_symbol, "TKN")
        self.assertEqual(pair.token_address, "0x1111111111111111111111111111111111111111")
        self.assertEqual(
            pair.pair_created_at,
            datetime.fromtimestamp(1700000000, tz=timezone.utc),
        )

    def test_parse_pair_missing_created_at_returns_none(self) -> None:
        pair_data = {
            "baseToken": {
                "name": "TokenName",
                "symbol": "TKN",
                "address": "0x1111111111111111111111111111111111111111",
            },
            "pairAddress": "0x2222222222222222222222222222222222222222",
            "url": "https://dexscreener.com/ethereum/0x2222222222222222222222222222222222222222",
        }

        pair = DexscreenerClient._parse_pair(pair_data, "ethereum")

        self.assertIsNone(pair)

    def test_extract_socials_uses_profile_pair_and_description_fallbacks(self) -> None:
        profile = {
            "description": "Join https://t.me/mytoken and follow https://x.com/mytoken",
            "links": [],
        }
        pair_data = {
            "info": {
                "socials": [
                    {"platform": "twitter", "url": "https://x.com/pair_twitter"},
                ],
                "websites": [{"url": "https://mytoken.example"}],
            }
        }

        socials = DexscreenerClient._extract_socials(profile, pair_data)

        self.assertEqual(socials.telegram_link, "https://t.me/mytoken")
        self.assertEqual(socials.twitter_link, "https://x.com/pair_twitter")
        self.assertEqual(socials.website, "https://mytoken.example")


if __name__ == "__main__":
    unittest.main()
