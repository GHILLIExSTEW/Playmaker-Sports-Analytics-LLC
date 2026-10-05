# Proxmox deployment guide

This project is designed to run as a Python service on a Linux host, such as a Proxmox LXC or VM.

## 1. Prepare the Linux server

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git curl
```

## 2. Clone the repository

```bash
cd /opt
sudo git clone <your-repo-url> discord-bot
cd discord-bot
```

## 3. Create the environment

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and fill in:

- `DISCORD_TOKEN`
- `APPLICATION_ID`
- `GUILD_ID`
- `SUPABASE_URL`
- `SUPABASE_KEY`
- `API_SPORTS_KEY` (required for player lookup and cache sync)
- `OPENAI_API_KEY`
- `OPENAI_VISION_MODEL`
- `OPENAI_VISION_MODELS`
- `OFFICIAL_ROLE_IDS`
- `OPERATOR_ROLE_IDS`
- `OFFICIAL_CHANNEL_ID`
- `IMAGE_INPUT_CHANNEL_ID`
- `TEAM_STATS_CHANNEL_ID`

After applying `src/database/schema.sql` and starting the bot, use `/syncplayers`
once for each league that should have player autocomplete. Select the sport and
league by name. `/playerstats` then suggests cached players and events by name;
season or game statistics are fetched on first lookup and cached in Supabase.

## 4. Validate the Python app

```bash
. .venv/bin/activate
python -m compileall src
python -m pytest -q
```

## 5. Start the bot directly

```bash
. .venv/bin/activate
python -m src.bot
```

## 6. Run as a background service

Create a systemd service file:

```bash
sudo nano /etc/systemd/system/discord-bot.service
```

Contents:

```ini
[Unit]
Description=Official Play Discord Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/discord-bot
ExecStart=/opt/discord-bot/.venv/bin/python -m src.bot
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Then enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now discord-bot.service
sudo systemctl status discord-bot.service
```

## 7. Useful operational commands

```bash
sudo journalctl -u discord-bot.service -f
sudo systemctl restart discord-bot.service
sudo systemctl stop discord-bot.service
```

## 8. Recommended Proxmox notes

- Run the bot in a Debian LXC or Ubuntu VM.
- Keep the repo on a persistent disk or backup to ensure the `.env` stays intact.
- Put the bot in a dedicated Linux user account if you want tighter controls.
- Keep the `SUPABASE_URL`, `SUPABASE_KEY`, and `DISCORD_TOKEN` in the `.env` file only, never in the repo.
