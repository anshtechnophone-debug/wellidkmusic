# WELLIDK MUSIC

A Telegram group/channel voice-chat music bot using Pyrogram and PyTgCalls. Song search is powered by Jamendo and is restricted to tracks offered under **Creative Commons Attribution (CC BY)** or **Attribution-ShareAlike (CC BY-SA)**. Check each track's license and attribution requirements before using it.

## Features

- `/play <song name>` — search licensed tracks and choose a result
- `/pause`, `/resume`, `/skip`, `/stop`
- `/queue`, `/np`, `/volume <1-200>`
- Inline player controls
- Player duration, elapsed time, remaining time, and progress bar
- Automatically leaves the voice chat when the queue finishes
- Supports group and channel voice chats

## Requirements

- Python 3.10+
- FFmpeg installed and available as `ffmpeg`
- Telegram bot token from BotFather
- Telegram API ID and API hash from my.telegram.org
- A separate Telegram user session string (the user account joins the voice chat)
- Jamendo API client ID

The voice chat must already be active. Add the bot to the group/channel with permission to read commands and post messages. The user account represented by `SESSION_STRING` must be able to join and speak in the voice chat. For channel voice chats, make sure the account has the required channel permissions.

## Install

```bash
git clone https://github.com/anshtechnophone-debug/wellidkmusic.git
cd wellidkmusic
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
apt update
apt install -y ffmpeg
cp .env.example .env
nano .env
```

Fill in every value in `.env`. Obtain a Jamendo API client ID from the Jamendo developer portal. Never publish your bot token, API hash, or session string.

Generate a user session string if you do not already have one:

```bash
set -a
source .env
set +a
python generate_session.py
```

Copy the printed session string into `SESSION_STRING` in `.env`, then start:

```bash
python bot.py
```

## Commands

| Command | Action |
|---|---|
| `/play <name>` | Search licensed music |
| `/pause` | Pause |
| `/resume` | Resume |
| `/skip` | Skip current track |
| `/stop` | Stop and clear queue |
| `/queue` | Show upcoming tracks |
| `/np` | Show current player |
| `/volume 80` | Set volume (1–200) |

## Notes

- This project intentionally does not rip audio from YouTube or other services. Search results come from Jamendo's CC BY / CC BY-SA catalog.
- The Telegram user session is powerful. Keep it private and revoke it if it is exposed.
- Keep FFmpeg installed on the server.
