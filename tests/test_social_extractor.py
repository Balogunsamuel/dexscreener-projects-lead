from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

try:
    from src.models import SocialLinks
    from src.social_extractor import SocialExtractor
except ModuleNotFoundError:  # pragma: no cover - environment dependent
    SocialLinks = None
    SocialExtractor = None


@unittest.skipIf(
    SocialExtractor is None or SocialLinks is None,
    "Social extractor tests require project dependencies.",
)
class SocialExtractorTests(unittest.IsolatedAsyncioTestCase):
    async def test_validate_and_enrich_strict_mode_discards_invalid_twitter(self) -> None:
        extractor = SocialExtractor(strict_validation=True)
        try:
            extractor.validate_telegram_link = AsyncMock(return_value=True)
            extractor.validate_twitter_link = AsyncMock(return_value=False)

            out = await extractor.validate_and_enrich(
                SocialLinks(
                    telegram_link="https://t.me/token_group",
                    twitter_link="https://x.com/token",
                    website="https://www.example.com/path",
                )
            )

            self.assertEqual(out.telegram_link, "https://t.me/token_group")
            self.assertIsNone(out.twitter_link)
            self.assertEqual(out.website, "example.com")
        finally:
            await extractor.close()

    async def test_validate_and_enrich_non_strict_keeps_twitter(self) -> None:
        extractor = SocialExtractor(strict_validation=False)
        try:
            extractor.validate_telegram_link = AsyncMock(return_value=True)
            extractor.validate_twitter_link = AsyncMock(return_value=False)

            out = await extractor.validate_and_enrich(
                SocialLinks(
                    telegram_link="https://t.me/token_group",
                    twitter_link="https://x.com/token",
                    website="example.com",
                )
            )

            self.assertEqual(out.twitter_link, "https://x.com/token")
            self.assertEqual(out.website, "example.com")
        finally:
            await extractor.close()


if __name__ == "__main__":
    unittest.main()
