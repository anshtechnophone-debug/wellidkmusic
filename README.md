# WELLIDK MUSIC

A Telegram group/channel voice-chat music bot using Pyrogram, Telethon and PyTgCalls. Song search is powered by Jamendo and is restricted to tracks offered under Creative Commons Attribution (CC BY) or Attribution-ShareAlike (CC BY-SA). Check each track's license and attribution requirements before using it.

## Features

- /play <song name> — search licensed tracks and choose a result
- /pause, /resume, /skip, /stop
- /queue, /np, /volume <1-200>
- /newaccount — connect a voice-player account using a private Telegram QR login
- /accounts — list connected player accounts
- /myid — show your Telegram numeric user ID for OWNER_ID setup
- Inline player controls (group admins and the configured owner only)
- Independent queue and controls for every group
- Multiple simultaneous voice chats using separately authorized player accounts
- FLAC downloads where available, plus the highest built-in PyTgCalls audio preset
- Track progress display and automatic leave when a queue finishes

## Connect player accounts from Telegram

You do not need to generate or paste session strings manually. Once the bot is running:

1. Set OWNER_ID in .env to your numeric Telegram user ID. You can get it by sending /myid to the bot, then restart the bot.
2. Open the bot's private chat and send /newaccount.
3. The bot sends a temporary QR code in that private chat.
4. On the Telegram account you want to use as a player, open Settings → Devices → Link Desktop Device and scan the QR code.
5. The bot confirms the account and stores its session locally. Use /accounts to see connected player accounts.

**Security:** Only the configured owner can run account-management commands, and QR login is accepted only in private chat. Never forward the QR code; it is a temporary account-login credential. The bot does not ask for OTPs or two-step passwords in chat. Accounts that require an additional password step are not saved by this flow. Saved session strings are stored locally in plaintext, with restrictive directory/file permissions (0700/0600), and the data/ directory is excluded from Git. This is access control, not encryption; protect VPS access and backups accordingly.

Each player account can serve one active voice chat at a time. Connect a separate account for each additional simultaneous group. The project supports up to 10 player accounts.

## Requirements

- Python 3.10+
- FFmpeg installed and available as ffmpeg
- Telegram bot token from BotFather
- Telegram API ID and API hash from my.telegram.org
- Jamendo API client ID
- Your numeric Telegram user ID for OWNER_ID

API_ID and API_HASH are required once in .env because Pyrogram needs them to initialize the bot client and the QR player login. They are not requested inside the bot. Keep them private.

The voice chat must already be active. Add the bot to the group/channel with permission to read commands and post messages. Each player account must be able to join and speak in its assigned voice chat.

## Install on Termux

```bash
pkg update && pkg upgrade -y
pkg install git python ffmpeg clang rust nano -y
git clone https://github.com/anshtechnophone-debug/wellidkmusic.git
cd wellidkmusic
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
cp .env.example .env
nano .env
```

Fill in BOT_TOKEN, API_ID, API_HASH, JAMENDO_CLIENT_ID, and OWNER_ID. You can leave SESSION_STRINGS empty if you plan to connect players using /newaccount. Start the bot:

```bash
python bot.py
```

## Commands

| Command | Action |
|---|---|
| /play <name> | Search licensed music |
| /pause | Pause |
| /resume | Resume |
| /skip | Skip current track |
| /stop | Stop and clear queue |
| /queue | Show upcoming tracks |
| /np | Show current player |
| /volume 80 | Set volume (1–200) |
| /newaccount | Owner-only QR account connection in private chat |
| /accounts | Owner-only connected-account list |
| /myid | Show your numeric Telegram user ID |

## Playback permissions

In groups, playback commands and inline controls are restricted to group administrators and the configured `OWNER_ID`. In private chats, only the configured owner can control playback.

## Audio quality

The player requests FLAC downloads from Jamendo and PyTgCalls' Studio preset. FLAC avoids another lossy MP3 encode, but cannot restore detail missing from the artist's original upload. Telegram's voice transport and listeners' network/device audio can still affect the sound.

## Notes

- This project intentionally does not rip audio from YouTube or other services. Search results come from Jamendo's CC BY / BY-SA catalog.
- Each connected player account is a separate Telegram authorization. Never share its session or the local data/ directory.
- Keep FFmpeg installed. A Linux VPS is recommended for reliable continuous multi-group playback; Android/Termux may have native-library or background-process limitations.
