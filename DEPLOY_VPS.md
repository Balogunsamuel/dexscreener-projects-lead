# VPS Deployment (24/7)

This project is designed to run continuously as a long-lived process.
For VPS hosting, use `systemd` so the bot auto-starts on boot and restarts on failure.

## 1. Prepare the server (Ubuntu)

```bash
sudo apt update
sudo apt install -y git python3 python3-venv
```

## 2. Clone and install

```bash
git clone <your-repo-url> dexscreener
cd dexscreener
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## 3. Configure runtime secrets

Create `.env` in the project root with your production values.

Required at minimum:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHANNEL_ID`

If you want Telegram admin extraction enabled, also set:
- `ENABLE_TELEGRAM_ADMIN_EXTRACTION=true`
- `TELEGRAM_API_ID`
- `TELEGRAM_API_HASH`
- `TELEGRAM_PHONE`

## 4. Create Telethon session once (only if admin extraction is enabled)

`Telethon` needs an authenticated session file (`dexbot_session.session`).
Create it once in an interactive shell:

```bash
source .venv/bin/activate
python -m src.main
```

Complete Telegram login prompts, wait until startup succeeds, then stop with `Ctrl+C`.

If you do not need admin extraction, set:

```env
ENABLE_TELEGRAM_ADMIN_EXTRACTION=false
```

## 5. Install as a systemd service

The repo includes an installer script:

```bash
chmod +x scripts/install_systemd_service.sh
sudo ./scripts/install_systemd_service.sh
```

By default, it installs `dexscreener-lead-bot.service` using:
- current repo directory as `WorkingDirectory`
- current user (or `SUDO_USER`) as service user
- `.venv/bin/python -m src.main` as `ExecStart`

## 6. Verify and monitor

```bash
sudo systemctl status dexscreener-lead-bot
sudo journalctl -u dexscreener-lead-bot -f
```

## Operations

Restart:

```bash
sudo systemctl restart dexscreener-lead-bot
```

Stop / start:

```bash
sudo systemctl stop dexscreener-lead-bot
sudo systemctl start dexscreener-lead-bot
```

Enable at boot (already done by installer):

```bash
sudo systemctl enable dexscreener-lead-bot
```

## Deploy updates

```bash
cd dexscreener
git pull
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart dexscreener-lead-bot
```

## Troubleshooting

- Service exits immediately:
  - check `.env` exists and required vars are set
  - check Python path: `.venv/bin/python`
- No admin usernames extracted:
  - verify `ENABLE_TELEGRAM_ADMIN_EXTRACTION=true`
  - verify `dexbot_session.session` exists in project root
- Inspect logs:
  - `sudo journalctl -u dexscreener-lead-bot -n 200 --no-pager`
