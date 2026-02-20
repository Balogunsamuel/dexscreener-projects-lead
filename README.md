# 🚀 Dexscreener Lead Bot

A production-ready Telegram bot that monitors **Dexscreener** for newly created token pairs across **Ethereum**, **BSC**, and **Base**, extracts founder contact intelligence (Telegram admins, Twitter, website, deployer wallet), and sends formatted lead notifications to a private Telegram channel.

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

1. **Token Profiles Polling**: The bot polls the `GET /token-profiles/latest/v1` endpoint every 30 seconds. This returns the most recently updated token profiles across all chains. We filter for `ethereum`, `bsc`, and `base` chain IDs.

2. **Pair Details Enrichment**: For each new token discovered, we call `GET /latest/dex/pairs/{chainId}/{pairId}` or `GET /token-pairs/v1/{chainId}/{tokenAddress}` to get the full pair data including `pairCreatedAt` timestamp, social links, and websites.

3. **Freshness Filter**: Only tokens with `pairCreatedAt` less than 15 minutes ago are processed further.

4. **Social & Admin Extraction**: For qualifying tokens, we extract Telegram links, resolve admin usernames via Telethon, find Twitter/X links, and look up the deployer wallet via block explorer APIs.

5. **Notification**: Tokens passing all filters are posted to the configured Telegram channel.

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
├── .env.example                # Environment variable template
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
cp .env.example .env
# Edit .env with your actual credentials
```

### 4. Run

```bash
python -m src.main
```

---

## Environment Variables

| Variable                | Description                                                |
| ----------------------- | ---------------------------------------------------------- |
| `TELEGRAM_BOT_TOKEN`    | Bot token from @BotFather                                  |
| `TELEGRAM_CHANNEL_ID`   | Channel/chat ID for notifications (e.g., `-1001234567890`) |
| `TELEGRAM_API_ID`       | Telegram API ID from my.telegram.org                       |
| `TELEGRAM_API_HASH`     | Telegram API hash from my.telegram.org                     |
| `TELEGRAM_PHONE`        | Phone number for Telethon session                          |
| `ETHERSCAN_API_KEY`     | Etherscan API key                                          |
| `BASESCAN_API_KEY`      | BaseScan API key                                           |
| `BSCSCAN_API_KEY`       | BscScan API key                                            |
| `POLL_INTERVAL_SECONDS` | Dexscreener polling interval (default: 30)                 |
| `MAX_TOKEN_AGE_MINUTES` | Max pair age to process (default: 15)                      |
| `DATABASE_PATH`         | SQLite database path (default: `data/leads.db`)            |

---

## Filtering Logic (MVP)

A token is only processed and notified when ALL of these conditions are met:

1. ✅ Chain is **Ethereum**, **BSC**, or **Base**
2. ✅ `pairCreatedAt` is less than **15 minutes** ago
3. ✅ Has a **public Telegram group** link
4. ✅ Has at least **one visible admin** username

---

## Rate Limits

| Service                      | Limit       | Strategy                     |
| ---------------------------- | ----------- | ---------------------------- |
| Dexscreener (token profiles) | 60 req/min  | Poll every 30s               |
| Dexscreener (pair details)   | 300 req/min | Batch with delays            |
| Etherscan/BscScan/BaseScan   | 5 req/sec   | Token bucket limiter         |
| Telegram Bot API             | 30 msg/sec  | Queued sending               |
| Telethon                     | Varies      | Built-in flood wait handling |

---

## License

MIT
