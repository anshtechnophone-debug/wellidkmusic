# WELLIDK MUSIC

A Telegram group/channel voice-chat music bot using Pyrogram and PyTgCalls. Song search is powered by Jamendo and is restricted to tracks offered under Creative Commons Attribution (CC BY) or Attribution-ShareAlike (CC BY-SA). Check each track's license and attribution requirements before using it.

## Features

- /play <song name> — search licensed tracks and choose a result
- /pause, /resume, /skip, /stop
- /queue, /np, /volume <1-200>
- Inline player controls
- Independent queue and controls for every group
- Multiple simultaneous voice chats using separately authorized player sessions
- FLAC downloads where available, plus the highest built-in PyTgCalls audio preset (Studio: 96 kHz stereo input)
- Track progress display and automatic leave when a queue finishes

## Multiple groups at once

Each active group uses its own separately authorized session of the same dedicated player Telegram account. The pool supports up to 10 sessions; the number of simultaneous groups is limited by how many session strings you configure. Do not paste the same session string more than once.

Pyrogram documents parallel sessions for the same account, and PyTgCalls provides a multiple-client example. See the project documentation before increasing the pool size.

## Requirements

- Python 3.10+
- FFmpeg installed and available as ffmpeg
- Telegram bot token from BotFather
- Telegram API ID and API hash from my.telegram.org
- One or more separate Telegram user session strings
- Jamendo API client ID

The voice chat must already be active. Add the bot to the group/channel with permission to read commands and post messages. The player account must be able to join and speak in each voice chat.

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

Fill in BOT_TOKEN, API_ID, API_HASH, and JAMENDO_CLIENT_ID first. Then generate the player sessions:

```bash
set -a
source .env
set +a
python generate_session.py
```

Choose how many groups you want to play in simultaneously (up to 10). Log in to the same dedicated player account for each separate session. Copy the generated SESSION_STRINGS line into .env. Keep it private; never publish your bot token, API hash, or session strings.

Start the bot:

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

## Audio quality

The player requests FLAC downloads from Jamendo and PyTgCalls' Studio preset (96 kHz, stereo). FLAC avoids another lossy MP3 encode, but cannot restore detail missing from the artist's original upload. Telegram's voice transport and listeners' network/device audio can still affect the sound.

## Notes

- This project intentionally does not rip audio from YouTube or other services. Search results come from Jamendo's CC BY / CC BY-SA catalog.
- Every configured session must be a different authorization session. Never run the same session string in two clients or share it.
- Keep FFmpeg installed. A Linux VPS is recommended for reliable continuous multi-group playback; Android/Termux may have native-library or background-process limitations.
