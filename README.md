# 🚀 Dexscreener Lead Bot

A production-ready Telegram bot that monitors **Dexscreener** for newly created token pairs across **Ethereum**, **BSC**, **Base**, and **Solana**, extracts founder contact intelligence (Telegram admins, Twitter, website, deployer wallet), and sends formatted lead notifications to a private Telegram channel.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────┐
│                  Main Orchestrator               │
│              (src/main.py - asyncio)             │
├────────┬──────────┬──────────┬──────────┬────────┤
│ Token  │ Social   │ Telegram │ Wallet   │ Notif  │
│ Disc.  │ Extract  │ Admin    │ Lookup   │ Engine │
│        │          │ (Teleth) │          │        │
├────────┴──────────┴──────────┴──────────┴────────┤
│              SQLite Persistence Layer             │
│                   (database.py)                   │
└─────────────────────────────────────────────────┘
```

### How Dexscreener Data is Monitored

1. **Token Profiles Polling**: The bot polls the `GET /token-profiles/latest/v1` endpoint every 30 seconds. This returns the most recently updated token profiles across all chains. We filter for tracked chain IDs (`ethereum`, `bsc`, `base`, `solana` by default).

2. **Pair Details Enrichment**: For each new token discovered, we call `GET /latest/dex/pairs/{chainId}/{pairId}` or `GET /token-pairs/v1/{chainId}/{tokenAddress}` to get the full pair data including `pairCreatedAt` timestamp, social links, and websites.

3. **Freshness Filter**: Only tokens with `pairCreatedAt` less than 15 minutes ago are processed further.

4. **Social & Admin Extraction**: For qualifying tokens, we extract Telegram links, resolve admin usernames via Telethon (optional), find Twitter/X links, and look up the deployer wallet via block explorer APIs.

5. **Notification**: Tokens passing configured filters are posted to the configured Telegram channel.

---

## Project Structure

```
dexscreener/
├── src/
│   ├── __init__.py
│   ├── main.py                 # Entry point & orchestrator
│   ├── config.py               # Environment & configuration
│   ├── database.py             # SQLite persistence layer
│   ├── models.py               # Pydantic data models
│   ├── dexscreener.py          # Dexscreener API client
│   ├── social_extractor.py     # Social link extraction (TG, Twitter, Website)
│   ├── telegram_admin.py       # Telethon-based admin extraction
│   ├── wallet_lookup.py        # Block explorer deployer wallet lookup
│   ├── notifier.py             # Telegram channel notification
│   └── utils.py                # Rate limiter, retry logic, helpers
├── .env                        # Local environment configuration (not committed)
├── requirements.txt            # Python dependencies
├── pyproject.toml              # Project metadata
└── README.md                   # This file
```

---

## Setup

### 1. Prerequisites

- Python 3.11+
- A Telegram Bot token (from [@BotFather](https://t.me/BotFather))
- Telegram API credentials (from [my.telegram.org](https://my.telegram.org)) for Telethon
- Block explorer API keys:
  - [Etherscan](https://etherscan.io/apis) (also works for Base via basescan.org)
  - [BscScan](https://bscscan.com/apis)

### 2. Install Dependencies

```bash
cd dexscreener
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure Environment

```bash
# Edit .env with your credentials and runtime flags
```

### 4. Run

```bash
python -m src.main
```

---

## Environment Variables

| Variable | Description |
| ------------------------------- | ---------------------------------------------------------- |
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather |
| `TELEGRAM_CHANNEL_ID` | Channel/chat ID for notifications (e.g., `-1001234567890`) |
| `ENABLE_TELEGRAM_ADMIN_EXTRACTION` | Enable Telethon-based admin extraction (`true`/`false`) |
| `TELEGRAM_API_ID` | Telegram API ID from my.telegram.org (needed when admin extraction is enabled) |
| `TELEGRAM_API_HASH` | Telegram API hash from my.telegram.org (needed when admin extraction is enabled) |
| `TELEGRAM_PHONE` | Phone number for Telethon session (needed when admin extraction is enabled) |
| `ETHERSCAN_API_KEY` | Etherscan API key |
| `BASESCAN_API_KEY` | BaseScan API key |
| `BSCSCAN_API_KEY` | BscScan API key |
| `POLL_INTERVAL_SECONDS` | Dexscreener polling interval (default: `30`) |
| `MAX_TOKEN_AGE_MINUTES` | Max pair age to process (default: `15`) |
| `DATABASE_PATH` | SQLite database path (default: `data/leads.db`) |
| `LOG_LEVEL` | Log level (default: `INFO`) |
| `ALLOW_TEST_LEADS` | If `true`, bypass strict filtering and allow test leads |
| `REGISTER_SKIPPED_TOKENS` | Record skipped tokens in DB to prevent reprocessing |
| `ENABLE_WALLET_LOOKUP` | Enable/disable deployer wallet lookup |
| `STRICT_SOCIAL_VALIDATION` | Discard invalid Twitter links when `true` |
| `REQUIRE_TELEGRAM_FOR_LEAD` | Require Telegram link in production filtering |
| `REQUIRE_VISIBLE_ADMIN_FOR_LEAD` | Require at least one visible admin |
| `REJECT_HIDDEN_ADMINS` | Skip leads with hidden admins and no visible admin |
| `TRACKED_CHAINS` | Comma-separated list of chains (example: `ethereum,bsc,base`) |
| `DEXSCREENER_PAIR_FETCH_CONCURRENCY` | Concurrent pair lookups per poll (default: `8`) |
| `DEXSCREENER_MAX_PROFILES_PER_POLL` | Max candidate profiles processed per poll (default: `120`) |
| `DEXSCREENER_FAIR_CHAIN_SAMPLING` | Balance profile selection across tracked chains (default: `true`) |
| `NOTIFICATION_RETRY_MAX_ATTEMPTS` | Max send attempts before dead-letter (default: `5`) |
| `NOTIFICATION_RETRY_BASE_DELAY_SECONDS` | Exponential backoff base delay (default: `60`) |
| `NOTIFICATION_RETRY_MAX_DELAY_SECONDS` | Cap for retry delay (default: `1800`) |
| `NOTIFICATION_RETRY_BATCH_SIZE` | Max due retries processed per poll (default: `25`) |

---

## Filtering Logic

By default (`ALLOW_TEST_LEADS=false`), a token is processed and notified when all enabled rules pass:

1. ✅ Chain is tracked
2. ✅ `pairCreatedAt` is less than `MAX_TOKEN_AGE_MINUTES`
3. ✅ `REQUIRE_TELEGRAM_FOR_LEAD=true` requires a public Telegram link
4. ✅ `REQUIRE_VISIBLE_ADMIN_FOR_LEAD=true` requires at least one visible admin
5. ✅ `REJECT_HIDDEN_ADMINS=true` rejects leads where admins are hidden and none are visible

Set `ALLOW_TEST_LEADS=true` to bypass strict filters for test runs.

If you are seeing mostly Solana leads, set `TRACKED_CHAINS=ethereum,bsc,base` or keep Solana but leave `DEXSCREENER_FAIR_CHAIN_SAMPLING=true` to balance sampling.

---

## Notification Retry Policy

- New leads are stored first, then notified.
- If notification fails, the lead is queued with exponential backoff and retry metadata.
- Retries stop after `NOTIFICATION_RETRY_MAX_ATTEMPTS`; failed leads are marked dead-letter and excluded from further retries.
- During schema upgrade to retry-queue mode, historical `notified=0` rows are marked notified once to avoid replaying old leads.

---

## Rate Limits

| Service | Limit | Strategy |
| ---------------------------- | ----------- | ---------------------------- |
| Dexscreener (token profiles) | 60 req/min | Poll every 30s |
| Dexscreener (pair details) | 300 req/min | Concurrency + token-bucket limiter |
| Etherscan/BscScan/BaseScan | 5 req/sec | Token bucket limiter |
| Telegram Bot API | 30 msg/sec | Retry on `RetryAfter` |
| Telethon | Varies | Built-in flood wait handling |

---

## Testing

```bash
python3 -m unittest discover -s tests -v
```

The repository includes tests for:

1. Dexscreener parser/social extraction helpers
2. DB insertion idempotency + notification retry/dead-letter state transitions
3. Notifier HTML safety formatting
4. End-to-end retry flow (fail once, then succeed)

---

## Security Notes

1. Never commit real credentials from `.env`.
2. Rotate any credential that was ever committed accidentally.
3. Keep `.env` local and outside version control.

---

## License

MIT
