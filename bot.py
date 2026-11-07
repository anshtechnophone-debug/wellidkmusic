import asyncio
import logging
import os
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Optional

import aiohttp
from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.enums import ChatMemberStatus
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from pytgcalls import filters as call_filters
from pytgcalls.types import StreamEnded
from player_pool import MultiCallPool
from account_manager import AccountManager
from youtube_source import search_youtube_tracks, download_youtube_track

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
SESSION_STRINGS = [value.strip() for value in os.getenv("SESSION_STRINGS", "").split(",") if value.strip()]
JAMENDO_CLIENT_ID = os.getenv("JAMENDO_CLIENT_ID", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)

if not all((BOT_TOKEN, API_ID, API_HASH, JAMENDO_CLIENT_ID)):
    raise RuntimeError(
        "Missing BOT_TOKEN, API_ID, API_HASH or JAMENDO_CLIENT_ID in .env"
    )
if len(SESSION_STRINGS) > 10:
    raise RuntimeError("SESSION_STRINGS supports up to 10 separate authorized sessions.")
if len(set(SESSION_STRINGS)) != len(SESSION_STRINGS):
    raise RuntimeError("Each player must have a different SESSION_STRING. Do not repeat one.")

BASE_DIR = Path(__file__).resolve().parent
MEDIA_DIR = BASE_DIR / "media"
MEDIA_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("wellidkmusic")

bot = Client(
    "wellidkmusic_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
)
users = [
    Client(
        f"wellidkmusic_player_{index}",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=session_string,
    )
    for index, session_string in enumerate(SESSION_STRINGS, start=1)
]
calls = MultiCallPool(users)
account_manager = AccountManager(
    bot=bot,
    call_pool=calls,
    api_id=API_ID,
    api_hash=API_HASH,
    owner_id=OWNER_ID,
    data_dir=BASE_DIR / "data",
)


@bot.on_message(filters.command("newaccount") & filters.private)
async def new_account_command(_, message):
    await account_manager.handle_new_account(_, message)


@bot.on_message(filters.command("accounts") & filters.private)
async def accounts_command(_, message):
    await account_manager.handle_accounts(_, message)


queues = defaultdict(deque)
players = {}
progress_tasks = {}
locks = defaultdict(asyncio.Lock)



async def can_control(chat, user):
    """Allow the configured owner or administrators to control group playback."""
    if not user:
        return False
    if OWNER_ID and user.id == OWNER_ID:
        return True
    if chat.type == "private":
        return False
    try:
        member = await bot.get_chat_member(chat.id, user.id)
        return member.status in (ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR)
    except Exception:
        log.exception("Could not verify playback permissions for user %s", user.id)
        return False


async def reject_control(message):
    if not await can_control(message.chat, message.from_user):
        await message.reply_text("⛔ Only group admins or the configured bot owner can control playback.")
        return True
    return False

def fmt_time(seconds: Optional[float]) -> str:
    if seconds is None:
        return "LIVE"
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def progress_bar(position: float, duration: int, width: int = 12) -> str:
    if duration <= 0:
        return "🔴 LIVE"
    filled = min(width, int(width * position / duration))
    return "━" * filled + "●" + "─" * (width - filled)


def player_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⏸ Pause", callback_data="player:pause"),
                InlineKeyboardButton("▶️ Resume", callback_data="player:resume"),
                InlineKeyboardButton("⏭ Skip", callback_data="player:skip"),
            ],
            [
                InlineKeyboardButton("🔉 -10", callback_data="player:vol_down"),
                InlineKeyboardButton("🔊 +10", callback_data="player:vol_up"),
                InlineKeyboardButton("⏹ Stop", callback_data="player:stop"),
            ],
        ]
    )


def render_player(chat_id: int) -> str:
    track = players.get(chat_id)
    if not track:
        return "🎵 Nothing is playing right now."
    duration = int(track.get("duration") or 0)
    elapsed_now = track.get("paused_elapsed", 0) if track.get("paused") else int(time.monotonic() - track["started_at"])
    elapsed = min(duration, int(elapsed_now))
    remaining = max(0, duration - elapsed) if duration else 0
    return (
        "🎧 **WELLIDK MUSIC PLAYER**\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🎵 **{track['title']}**\n"
        f"👤 Artist: {track['artist']}\n"
        f"⏱ {fmt_time(elapsed)} {progress_bar(elapsed, duration)} {fmt_time(duration)}\n"
        f"⌛ Remaining: {fmt_time(remaining)}\n"
        f"🔊 Volume: {track.get('volume', 100)}%\n"
        f"📄 [Usage info]({track['license_url']}) · [Track source]({track['source_url']})\n"
        "━━━━━━━━━━━━━━━━━━"
    )


async def search_tracks(query: str):
    url = "https://api.jamendo.com/v3.0/tracks/"
    params = {
        "client_id": JAMENDO_CLIENT_ID,
        "format": "json",
        "limit": 8,
        "search": query,
        "audioformat": "flac",
        "include": "licenses",
        "audiodlformat": "flac",
    }
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, params=params) as response:
            response.raise_for_status()
            payload = await response.json()
    return [
        track for track in payload.get("results", [])
        if track.get("audiodownload_allowed") is True
        and track.get("audiodownload")
        and (
            "/licenses/by/" in (track.get("license_ccurl") or "").lower()
            or "/licenses/by-sa/" in (track.get("license_ccurl") or "").lower()
        )
    ]


async def download_track(track: dict) -> Path:
    download_url = track.get("audiodownload")
    if track.get("audiodownload_allowed") is not True or not download_url:
        raise RuntimeError("The artist has not enabled downloads for this track.")
    file_path = MEDIA_DIR / (str(track["id"]) + ".flac")
    if file_path.exists() and file_path.stat().st_size > 20_000:
        return file_path

    temporary_path = file_path.with_suffix(".flac.part")
    temporary_path.unlink(missing_ok=True)
    timeout = aiohttp.ClientTimeout(total=180)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(download_url) as response:
                response.raise_for_status()
                with temporary_path.open("wb") as output:
                    async for chunk in response.content.iter_chunked(256 * 1024):
                        output.write(chunk)
        if temporary_path.stat().st_size < 20_000:
            raise RuntimeError("The audio download was incomplete.")
        temporary_path.replace(file_path)
        return file_path
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


async def update_progress(chat_id: int):
    while chat_id in players:
        track = players[chat_id]
        try:
            await bot.edit_message_text(
                chat_id,
                track["message_id"],
                render_player(chat_id),
                reply_markup=player_keyboard(),
                disable_web_page_preview=True,
            )
        except Exception:
            pass
        await asyncio.sleep(8)


async def stop_progress(chat_id: int):
    task = progress_tasks.pop(chat_id, None)
    if task and task is not asyncio.current_task():
        task.cancel()


async def start_track(chat_id: int, track: dict, message_id: int):
    if track.get("_source") == "youtube":
        audio_source = await download_youtube_track(
            track["webpage_url"], track["video_id"], str(MEDIA_DIR)
        )
        await calls.play(chat_id, audio_source)
        stored_path = audio_source
        license_url = track.get("license_url") or "https://www.youtube.com/t/terms"
        source_url = track.get("webpage_url") or "https://www.youtube.com"
    else:
        audio_path = await download_track(track)
        await calls.play(chat_id, str(audio_path))
        stored_path = str(audio_path)
        license_url = track.get("license_ccurl", "https://creativecommons.org/licenses/by/4.0/")
        source_url = track.get("shareurl") or track.get("shorturl") or "https://www.jamendo.com"
    players[chat_id] = {
        "id": str(track["id"]),
        "title": track.get("name", "Unknown title"),
        "artist": track.get("artist_name", "Unknown artist"),
        "duration": int(track.get("duration") or 0),
        "license_url": license_url,
        "source_url": source_url,
        "started_at": time.monotonic(),
        "message_id": message_id,
        "path": stored_path,
        "volume": 100,
        "paused": False,
        "paused_elapsed": 0,
    }
    await stop_progress(chat_id)
    progress_tasks[chat_id] = asyncio.create_task(update_progress(chat_id))
    await bot.edit_message_text(
        chat_id,
        message_id,
        render_player(chat_id),
        reply_markup=player_keyboard(),
        disable_web_page_preview=True,
    )


async def play_next(chat_id: int, message_id: Optional[int] = None):
    async with locks[chat_id]:
        if not queues[chat_id]:
            await stop_progress(chat_id)
            players.pop(chat_id, None)
            try:
                await calls.leave_call(chat_id)
            except Exception:
                pass
            if message_id:
                await bot.edit_message_text(
                    chat_id,
                    message_id,
                    "🏁 Queue finished. I left the voice chat."
                )
            return

        track = queues[chat_id].popleft()
        status_message_id = message_id or track.get("status_message_id")
        if not status_message_id:
            sent = await bot.send_message(chat_id, "⏳ Loading next track...")
            status_message_id = sent.id
        try:
            await start_track(chat_id, track, status_message_id)
        except Exception as exc:
            log.exception("Could not start queued track")
            await bot.send_message(chat_id, f"⚠️ Could not play **{track.get('name', 'track')}**: {exc}")
            await play_next_unlocked(chat_id, status_message_id)


async def play_next_unlocked(chat_id: int, message_id: int):
    if not queues[chat_id]:
        await stop_progress(chat_id)
        players.pop(chat_id, None)
        try:
            await calls.leave_call(chat_id)
        except Exception:
            pass
        return
    track = queues[chat_id].popleft()
    try:
        await start_track(chat_id, track, message_id)
    except Exception:
        log.exception("Could not start next queued track")
        await play_next_unlocked(chat_id, message_id)


@calls.on_update(call_filters.stream_end())
async def on_stream_end(_: MultiCallPool, update: StreamEnded):
    chat_id = update.chat_id
    await stop_progress(chat_id)
    players.pop(chat_id, None)
    await play_next(chat_id)


@bot.on_message(filters.command("start"))
async def start_command(_, message):
    log.info("Handling /start from user_id=%s", message.from_user.id if message.from_user else "unknown")
    await message.reply_text(
        "🎧 **WELLIDK MUSIC**\n\n"
        "Search YouTube and Jamendo, then play tracks in your group's or channel's active voice chat.\n\n"
        "**Commands**\n"
        "/play <song name> — search licensed tracks\n"
        "/pause /resume /skip /stop\n"
        "/queue /np /volume <1-200>\n"
        "/help — setup and commands",
        disable_web_page_preview=True,
    )


@bot.on_message(filters.command("myid") & filters.private)
async def myid_command(_, message):
    log.info("Handling /myid from user_id=%s", message.from_user.id if message.from_user else "unknown")
    await message.reply_text(f"Your Telegram user ID: `{message.from_user.id}`")


@bot.on_message(filters.command("help"))
async def help_command(_, message):
    await message.reply_text(
        "**🎵 WELLIDK MUSIC HELP**\n\n"
        "• /play <name> — search YouTube and Jamendo tracks\n"
        "• /pause — pause playback\n"
        "• /resume — resume playback\n"
        "• /skip — skip current track\n"
        "• /stop — clear queue and leave voice chat\n"
        "• /queue — show upcoming tracks\n"
        "• /np — show the current player\n"
        "• /volume <1-200> — change volume\n\n"
        "The group's or channel's voice chat must already be active. "
        "The assistant account must be able to join and speak.",
        disable_web_page_preview=True,
    )


@bot.on_message(filters.command("play"))
async def play_command(_, message):
    if len(message.command) < 2:
        return await message.reply_text("Usage: /play <song name>")
    query = " ".join(message.command[1:]).strip()
    status = await message.reply_text(f"🔎 Searching YouTube and Jamendo for **{query}**...")
    try:
        results = await search_youtube_tracks(query)
        try:
            jamendo_results = await search_tracks(query)
        except Exception:
            log.exception("Jamendo search failed; continuing with YouTube results")
            jamendo_results = []
        for item in jamendo_results:
            item["_source"] = "jamendo"
            item["id"] = f"jm_{item['id']}"
        results.extend(jamendo_results)
        results = results[:8]
        if not results:
            return await status.edit_text("No results found. Try another song title or artist.")
        if len(results) == 1:
            selected = results[0]
        else:
            rows = []
            for item in results[:8]:
                title = item.get("name", "Unknown")
                artist = item.get("artist_name", "Unknown artist")
                rows.append([InlineKeyboardButton(
                    f"{title[:35]} — {artist[:20]}",
                    callback_data=f"choose:{item['id']}"
                )])
            await status.edit_text(
                "🎼 **Choose a track**\nYouTube and Jamendo results. Play only audio you are authorized to rebroadcast.",
                reply_markup=InlineKeyboardMarkup(rows),
            )
            search_cache[message.chat.id] = {str(x["id"]): x for x in results}
            return
        queues[message.chat.id].append(selected)
        if message.chat.id not in players:
            await status.edit_text("⏳ Preparing your track...")
            await play_next(message.chat.id, status.id)
        else:
            await status.edit_text(
                f"➕ Added to queue: **{selected.get('name')}** — {selected.get('artist_name')}"
            )
    except Exception as exc:
        log.exception("Search/play failed")
        await status.edit_text(f"❌ Couldn't search or play that track: {exc}")


search_cache = defaultdict(dict)


@bot.on_callback_query(filters.regex(r"^choose:([A-Za-z0-9_-]+)$"))
async def choose_track(_, query):
    chat_id = query.message.chat.id
    track_id = query.matches[0].group(1)
    track = search_cache.get(chat_id, {}).get(track_id)
    if not track:
        return await query.answer("Search expired. Run /play again.", show_alert=True)
    await query.answer("Added to queue")
    queues[chat_id].append(track)
    if chat_id not in players:
        await query.message.edit_text("⏳ Preparing your selected track...")
        await play_next(chat_id, query.message.id)
    else:
        await query.message.edit_text(
            f"➕ Added to queue: **{track.get('name')}** — {track.get('artist_name')}"
        )


@bot.on_callback_query(filters.regex(r"^player:(pause|resume|skip|stop|vol_down|vol_up)$"))
async def player_controls(_, query):
    chat_id = query.message.chat.id
    action = query.matches[0].group(1)
    if not await can_control(query.message.chat, query.from_user):
        return await query.answer("Only group admins or the bot owner can control playback.", show_alert=True)
    try:
        if action == "pause":
            await calls.pause(chat_id)
            if chat_id in players:
                players[chat_id]["paused_elapsed"] = int(time.monotonic() - players[chat_id]["started_at"])
                players[chat_id]["paused"] = True
                await query.answer("Paused")
        elif action == "resume":
            await calls.resume(chat_id)
            if chat_id in players:
                players[chat_id]["started_at"] = time.monotonic() - players[chat_id].get("paused_elapsed", 0)
                players[chat_id]["paused"] = False
                await query.answer("Resumed")
        elif action == "skip":
            await query.answer("Skipping")
            try:
                await calls.leave_call(chat_id)
            except Exception:
                pass
            await stop_progress(chat_id)
            players.pop(chat_id, None)
            await play_next(chat_id)
        elif action == "stop":
            queues[chat_id].clear()
            await stop_progress(chat_id)
            players.pop(chat_id, None)
            try:
                await calls.leave_call(chat_id)
            except Exception:
                pass
            await query.message.edit_text("⏹ Playback stopped and queue cleared.")
            await query.answer("Stopped")
            return
        elif action in ("vol_down", "vol_up"):
            if chat_id not in players:
                return await query.answer("Nothing is playing.", show_alert=True)
            current = players[chat_id]["volume"]
            volume = max(1, min(200, current + (-10 if action == "vol_down" else 10)))
            await calls.change_volume_call(chat_id, volume)
            players[chat_id]["volume"] = volume
            await query.answer(f"Volume: {volume}%")
        await bot.edit_message_text(
            chat_id,
            query.message.id,
            render_player(chat_id),
            reply_markup=player_keyboard(),
        )
    except Exception as exc:
        await query.answer(f"Action failed: {exc}", show_alert=True)


@bot.on_message(filters.command("pause"))
async def pause_command(_, message):
    if await reject_control(message):
        return
    await calls.pause(message.chat.id)
    if message.chat.id in players:
        players[message.chat.id]["paused_elapsed"] = int(time.monotonic() - players[message.chat.id]["started_at"])
        players[message.chat.id]["paused"] = True
    await message.reply_text("⏸ Playback paused.")


@bot.on_message(filters.command("resume"))
async def resume_command(_, message):
    if await reject_control(message):
        return
    await calls.resume(message.chat.id)
    if message.chat.id in players:
        players[message.chat.id]["started_at"] = time.monotonic() - players[message.chat.id].get("paused_elapsed", 0)
        players[message.chat.id]["paused"] = False
    await message.reply_text("▶️ Playback resumed.")


@bot.on_message(filters.command("skip"))
async def skip_command(_, message):
    if await reject_control(message):
        return
    chat_id = message.chat.id
    try:
        await calls.leave_call(chat_id)
    except Exception:
        pass
    await stop_progress(chat_id)
    players.pop(chat_id, None)
    await play_next(chat_id)
    await message.reply_text("⏭ Skipped.")


@bot.on_message(filters.command("stop"))
async def stop_command(_, message):
    if await reject_control(message):
        return
    chat_id = message.chat.id
    queues[chat_id].clear()
    await stop_progress(chat_id)
    players.pop(chat_id, None)
    try:
        await calls.leave_call(chat_id)
    except Exception:
        pass
    await message.reply_text("⏹ Stopped playback and cleared the queue.")


@bot.on_message(filters.command("queue"))
async def queue_command(_, message):
    chat_id = message.chat.id
    upcoming = list(queues[chat_id])
    if not upcoming:
        return await message.reply_text("📭 The queue is empty.")
    lines = [f"{i}. {x.get('name')} — {x.get('artist_name')}" for i, x in enumerate(upcoming[:15], 1)]
    await message.reply_text("📜 **UP NEXT**\n" + "\n".join(lines))


@bot.on_message(filters.command("np"))
async def np_command(_, message):
    chat_id = message.chat.id
    if chat_id not in players:
        return await message.reply_text("🎵 Nothing is playing.")
    await message.reply_text(
        render_player(chat_id),
        reply_markup=player_keyboard(),
        disable_web_page_preview=True,
    )


@bot.on_message(filters.command("volume"))
async def volume_command(_, message):
    if await reject_control(message):
        return
    if len(message.command) < 2 or not message.command[1].isdigit():
        return await message.reply_text("Usage: /volume <1-200>")
    volume = max(1, min(200, int(message.command[1])))
    chat_id = message.chat.id
    if chat_id not in players:
        return await message.reply_text("Nothing is playing right now.")
    await calls.change_volume_call(chat_id, volume)
    players[chat_id]["volume"] = volume
    await message.reply_text(f"🔊 Volume set to {volume}%.")


async def main():
    started_users = []
    try:
        for player in users:
            await player.start()
            started_users.append(player)
        await bot.start()
        await account_manager.load_existing()
        await calls.start()
        me = await bot.get_me()
        log.info("Started as @%s with %s voice-player account(s)", me.username, len(calls.clients))
        print("WELLIDK MUSIC is running. Press Ctrl+C to stop.")
        await asyncio.Event().wait()
    finally:
        try:
            await calls.stop()
        except Exception:
            log.exception("Error while stopping voice clients")
        if bot.is_connected:
            await bot.stop()
        for player in reversed(account_manager.clients):
            try:
                if player.is_connected():
                    await player.disconnect()
            except Exception:
                log.exception("Error while disconnecting a saved player account")
        for player in reversed(started_users):
            if player.is_connected:
                await player.stop()


if __name__ == "__main__":
    # Pyrogram 2.0 captures the current event loop in Client.__init__.
    # The Client is created at module import time, so run main() on that
    # exact loop instead of asyncio.run(), which would create a second loop.
    loop = asyncio.get_event_loop()
    if bot.loop is not loop:
        raise RuntimeError("Pyrogram client and application are using different event loops.")
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        pass
    finally:
        if not loop.is_closed():
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()
