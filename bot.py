import asyncio
import logging
import json
from datetime import datetime, timedelta, timezone
import os
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Optional

import aiohttp
from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.enums import ChatMemberStatus, ParseMode
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from pytgcalls import filters as call_filters
from pytgcalls.types import StreamEnded
from player_pool import MultiCallPool
from account_manager import AccountManager
from youtube_source import search_youtube_tracks, download_youtube_track
from songsx_source import SongsxSource
from player_art import make_player_cover

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
SESSION_STRINGS = [value.strip() for value in os.getenv("SESSION_STRINGS", "").split(",") if value.strip()]
JAMENDO_CLIENT_ID = os.getenv("JAMENDO_CLIENT_ID", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)
SONGSX_BOT_USERNAME = os.getenv("SONGSX_BOT_USERNAME", "Songsx_bot")
PUBLIC_BOT_USERNAME = os.getenv("BOT_USERNAME", "MusicXyronrobot").lstrip("@")
XYRON_CHANNEL = os.getenv("XYRON_CHANNEL", "@xyronschannel")
AUDIO_CACHE_CHANNEL_ID = os.getenv("AUDIO_CACHE_CHANNEL_ID", "4363096287")

if not all((BOT_TOKEN, API_ID, API_HASH)):
    raise RuntimeError("Missing BOT_TOKEN, API_ID or API_HASH in .env")
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
songsx_source = SongsxSource(
    bot_username=SONGSX_BOT_USERNAME,
    cache_channel_id=AUDIO_CACHE_CHANNEL_ID,
    media_dir=MEDIA_DIR,
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



async def is_channel_member(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(XYRON_CHANNEL, user_id)
        return member.status in (
            ChatMemberStatus.OWNER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.MEMBER,
        ) or bool(getattr(member, "is_member", False))
    except Exception:
        log.exception("Could not verify channel membership for user %s", user_id)
        return False


def subscription_keyboard():
    return {
        "inline_keyboard": [
            [{"text": "📢 JOIN XYRON CHANNEL", "url": "https://t.me/xyronschannel", "style": "primary"}],
            [{"text": "✅ CONFIRM MEMBERSHIP", "callback_data": "subscription:confirm", "style": "success"}],
        ]
    }


async def send_subscription_prompt(user_id: int):
    return await telegram_api(
        "sendMessage",
        {
            "chat_id": user_id,
            "text": (
                "🔒 <b>One quick step before playing music</b>\n\n"
                "Join <b>XYRON's official channel</b>, then tap Confirm Membership. "
                "Once verified, you can use /play in your groups."
            ),
            "parse_mode": "HTML",
            "reply_markup": subscription_keyboard(),
            "disable_web_page_preview": True,
        },
    )


async def edit_song_choices(chat_id: int, message_id: int, results: list):
    rows = []
    for index, item in enumerate(results[:7]):
        title = str(item.get("name", "Unknown"))[:35]
        artist = str(item.get("artist_name", "Unknown artist"))[:20]
        rows.append([{
            "text": f"♫  {title} — {artist}",
            "callback_data": f"choose:{item['id']}",
            "style": "primary" if index % 2 == 0 else "success",
        }])
    return await telegram_api(
        "editMessageText",
        {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": "🎼 <b>CHOOSE YOUR TRACK</b>\nSelect a song to add it to the group player:",
            "parse_mode": "HTML",
            "reply_markup": {"inline_keyboard": rows},
            "disable_web_page_preview": True,
        },
    )


async def require_channel_membership(message) -> bool:
    user = message.from_user
    if not user:
        await message.reply_text("I couldn't identify your Telegram account. Please try again.")
        return False
    if await is_channel_member(user.id):
        return True
    try:
        await send_subscription_prompt(user.id)
        await message.reply_text(
            "🔒 Please join **@xyronschannel** and confirm your membership in my private chat. "
            "I've sent you the verification button."
        )
    except Exception:
        await message.reply_text(
            "🔒 To use XYRON MUSIC, first open @MusicXyronrobot in private chat and send /start. "
            "Then join @xyronschannel and confirm membership."
        )
    return False


@bot.on_callback_query(filters.regex(r"^subscription:confirm$"))
async def confirm_subscription(_, query):
    if await is_channel_member(query.from_user.id):
        await query.answer("Membership confirmed! Music is unlocked.", show_alert=True)
        try:
            await query.message.edit_text(
                "✅ <b>You're verified!</b>\n\n"
                "Add @MusicXyronrobot to your group and use <code>/play song name</code>.",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
    else:
        await query.answer(
            "I can't see your membership yet. Join @xyronschannel first, then try again.",
            show_alert=True,
        )


@bot.on_message(filters.new_chat_members)
async def bot_added_to_group(_, message):
    me = await bot.get_me()
    if not any(member.id == me.id for member in (message.new_chat_members or [])):
        return
    adder = message.from_user
    if not adder:
        return
    try:
        await send_subscription_prompt(adder.id)
        await message.reply_text(
            f"👋 Thanks for adding **@{PUBLIC_BOT_USERNAME}**! "
            "The person who added me must join @xyronschannel and confirm membership in my DM before using /play."
        )
    except Exception:
        await message.reply_text(
            f"👋 Thanks for adding **@{PUBLIC_BOT_USERNAME}**! "
            "Please open my private chat, send /start, then join @xyronschannel and confirm membership before using /play."
        )



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
    if not await require_channel_membership(message):
        return True
    if not await can_control(message.chat, message.from_user):
        await message.reply_text("⛔ Only group admins or the configured bot owner can control playback.")
        return True
    return False


async def ensure_player_in_group(chat_id: int):
    """Use the bot's invite permission to add the assigned voice account to this group."""
    if chat_id >= 0:
        raise RuntimeError("Music playback is available in groups, not private chats.")
    if not account_manager.clients:
        raise RuntimeError("No connected Telegram user account. Send /newaccount to WELLIDK in private chat first.")

    invite = await bot.create_chat_invite_link(
        chat_id,
        member_limit=1,
        expire_date=datetime.now(timezone.utc) + timedelta(minutes=5),
        name="WELLIDK voice player",
    )
    try:
        member = await calls.ensure_member(
            chat_id,
            invite.invite_link,
            preferred_client=account_manager.clients[0],
        )
        log.info("Voice player account %s joined/confirmed in group %s", member.id, chat_id)
        return member
    finally:
        try:
            await bot.revoke_chat_invite_link(chat_id, invite.invite_link)
        except Exception:
            log.debug("Could not revoke temporary voice-player invite", exc_info=True)

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


def player_keyboard(chat_id: int):
    track = players.get(chat_id, {})
    paused = bool(track.get("paused"))
    toggle = {
        "text": "▶  RESUME" if paused else "Ⅱ  PAUSE",
        "callback_data": "player:resume" if paused else "player:pause",
        "style": "success" if paused else "primary",
    }
    return {
        "inline_keyboard": [
            [
                {"text": "↻  REPLAY", "callback_data": "player:replay", "style": "success"},
                toggle,
            ],
            [
                {"text": "⏭  NEXT", "callback_data": "player:skip", "style": "primary"},
                {"text": "☷  QUEUE", "callback_data": "player:queue", "style": "primary"},
            ],
            [
                {"text": "－  VOLUME", "callback_data": "player:vol_down", "style": "primary"},
                {"text": "＋  VOLUME", "callback_data": "player:vol_up", "style": "primary"},
            ],
            [
                {"text": "■  STOP PLAYBACK", "callback_data": "player:stop", "style": "danger"},
            ],
        ]
    }


async def telegram_api(method: str, payload: dict, photo=None):
    """Call Bot API directly so Bot API 9.4 native button styles are preserved."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    timeout = aiohttp.ClientTimeout(total=45)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        if photo is not None:
            form = aiohttp.FormData()
            for key, value in payload.items():
                if isinstance(value, (dict, list)):
                    value = json.dumps(value, ensure_ascii=False)
                form.add_field(key, str(value))
            photo.seek(0)
            form.add_field(
                "photo", photo.read(), filename="xyron_music_player.png",
                content_type="image/png",
            )
            async with session.post(url, data=form) as response:
                data = await response.json()
        else:
            async with session.post(url, json=payload) as response:
                data = await response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram Bot API {method} failed: {data.get('description', 'unknown error')}")
    return data["result"]


async def send_player_card(chat_id: int, cover, caption: str):
    return await telegram_api(
        "sendPhoto",
        {
            "chat_id": chat_id,
            "caption": caption,
            "parse_mode": "HTML",
            "reply_markup": player_keyboard(chat_id),
        },
        photo=cover,
    )


async def edit_player_card(chat_id: int, message_id: int, caption: str, *, with_keyboard=True):
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "caption": caption,
        "parse_mode": "HTML",
    }
    if with_keyboard:
        payload["reply_markup"] = player_keyboard(chat_id)
    else:
        payload["reply_markup"] = {"inline_keyboard": []}
    return await telegram_api("editMessageCaption", payload)

def render_player(chat_id: int) -> str:
    track = players.get(chat_id)
    if not track:
        return "🎵 Nothing is playing right now."
    duration = int(track.get("duration") or 0)
    elapsed_now = track.get("paused_elapsed", 0) if track.get("paused") else int(time.monotonic() - track["started_at"])
    elapsed = min(duration, int(elapsed_now)) if duration else int(elapsed_now)
    remaining = max(0, duration - elapsed) if duration else 0
    esc = lambda value: str(value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    title = esc(track.get("title") or "Unknown title")
    artist = esc(track.get("artist") or "Unknown artist")
    requested_by = esc(track.get("requested_by") or "A group member")
    requested_username = esc(track.get("requested_by_username") or "")
    username = PUBLIC_BOT_USERNAME or "XYRON Music"
    bot_url = f"https://t.me/{PUBLIC_BOT_USERNAME}" if PUBLIC_BOT_USERNAME else "https://t.me/"
    status = "⏸ <b>PAUSED</b>" if track.get("paused") else "🟢 <b>NOW PLAYING</b>"
    if duration:
        progress = progress_bar(elapsed, duration, width=20)
        time_line = f"<code>{fmt_time(elapsed)}</code>  {progress}  <code>{fmt_time(duration)}</code>"
        remaining_line = f"⏳ <b>{fmt_time(remaining)}</b> left"
    else:
        time_line = f"<code>{fmt_time(elapsed)}</code>  🔴 LIVE"
        remaining_line = "⏳ Live stream"
    requester = f"{requested_by} {requested_username}".strip()
    return (
        f"🎧 <b>XYRON MUSIC</b>  ·  {status}\n"
        f"🎵 <b>{title}</b>\n"
        f"🎤 {artist}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"{time_line}\n"
        f"{remaining_line}  ·  🔊 {track.get('volume', 100)}%\n"
        f"👤 <b>Requested by:</b> {requester}\n"
        f"✨ <a href=\"{bot_url}\">{esc(username)}</a>"
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
            await edit_player_card(chat_id, track["message_id"], render_player(chat_id))
        except Exception:
            pass
        await asyncio.sleep(2)


async def stop_progress(chat_id: int):
    task = progress_tasks.pop(chat_id, None)
    if task and task is not asyncio.current_task():
        task.cancel()


async def start_track(chat_id: int, track: dict, message_id: int):
    # Make sure the exact MTProto account assigned to this voice chat is a group member.
    await ensure_player_in_group(chat_id)
    if track.get("_source") == "songsx":
        audio_path = await songsx_source.download_selected(track)
        await calls.play(chat_id, str(audio_path))
        stored_path = str(audio_path)
        license_url = ""
        source_url = f"https://t.me/{PUBLIC_BOT_USERNAME}" if PUBLIC_BOT_USERNAME else "https://t.me/"
        source_name = "songsx"
    elif track.get("_source") == "youtube":
        audio_source = await download_youtube_track(
            track["webpage_url"], track["video_id"], str(MEDIA_DIR)
        )
        await calls.play(chat_id, audio_source)
        stored_path = audio_source
        license_url = track.get("license_url") or "https://www.youtube.com/t/terms"
        source_url = track.get("webpage_url") or "https://www.youtube.com"
        source_name = "youtube"
    else:
        audio_path = await download_track(track)
        await calls.play(chat_id, str(audio_path))
        stored_path = str(audio_path)
        license_url = track.get("license_ccurl", "https://creativecommons.org/licenses/by/4.0/")
        source_url = track.get("shareurl") or track.get("shorturl") or "https://www.jamendo.com"
        source_name = "jamendo"
    players[chat_id] = {
        "id": str(track["id"]),
        "title": track.get("name", "Unknown title"),
        "artist": track.get("artist_name", "Unknown artist"),
        "requested_by": track.get("requested_by", "A group member"),
        "requested_by_username": track.get("requested_by_username", ""),
        "duration": int(track.get("duration") or 0),
        "license_url": license_url,
        "source_url": source_url,
        "source": source_name,
        "started_at": time.monotonic(),
        "message_id": message_id,
        "path": stored_path,
        "volume": 100,
        "paused": False,
        "paused_elapsed": 0,
    }
    cover = make_player_cover(
        title=players[chat_id]["title"],
        artist=players[chat_id]["artist"],
        requested_by=players[chat_id]["requested_by"],
        bot_username=PUBLIC_BOT_USERNAME,
    )
    sent = await send_player_card(chat_id, cover, render_player(chat_id))
    players[chat_id]["message_id"] = sent["message_id"]
    try:
        await bot.delete_messages(chat_id, message_id)
    except Exception:
        pass
    await stop_progress(chat_id)
    progress_tasks[chat_id] = asyncio.create_task(update_progress(chat_id))


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
            error_text = str(exc)
            if "no active group call" in error_text.lower():
                error_text = "No active voice chat. Start a group voice chat first, then run /play again."
            elif "invite" in error_text.lower() or "chat_admin_required" in error_text.lower() or "not enough rights" in error_text.lower():
                error_text = f"I couldn't invite the voice-player account. Make WELLIDK an admin with **Invite Users** permission. ({type(exc).__name__})"
            await bot.send_message(chat_id, f"⚠️ Could not play **{track.get('name', 'track')}**: {error_text}")
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


@bot.on_message(filters.command("start") & filters.private)
async def start_command(_, message):
    keyboard = subscription_keyboard()
    await telegram_api(
        "sendMessage",
        {
            "chat_id": message.chat.id,
            "text": (
                "🎧 <b>XYRON MUSIC</b>\n\n"
                "I'm your group's music player. Add <b>@MusicXyronrobot</b> to a group, "
                "start its voice chat, and use <code>/play song name</code>.\n\n"
                "Before playing, join <b>@xyronschannel</b> and confirm below.\n\n"
                "Use /help to see the commands."
            ),
            "parse_mode": "HTML",
            "reply_markup": keyboard,
            "disable_web_page_preview": True,
        },
    )


@bot.on_message(filters.command("myid") & filters.private)
async def myid_command(_, message):
    log.info("Handling /myid from user_id=%s", message.from_user.id if message.from_user else "unknown")
    await message.reply_text(f"Your Telegram user ID: `{message.from_user.id}`")


@bot.on_message(filters.command("help"))
async def help_command(_, message):
    await message.reply_text(
        "🎧 **XYRON MUSIC — HELP**\n\n"
        "**Start here**\n"
        "• Add @MusicXyronrobot to your group\n"
        "• Start the group's voice chat\n"
        "• Use /play song name\n\n"
        "**Commands**\n"
        "• /play <song name> — find and play a song\n"
        "• /pause — pause playback\n"
        "• /resume — resume playback\n"
        "• /skip — skip the current song\n"
        "• /stop — stop playback and clear the queue\n"
        "• /queue — show upcoming songs\n"
        "• /np — show the current player\n"
        "• /volume <1-200> — change volume\n\n"
        "Playback controls are also available as inline buttons on the player card. "
        "Join @xyronschannel and confirm membership before using music. "
        "The bot needs permission to invite its voice-player account; the voice chat must already be active.",
        disable_web_page_preview=True,
    )


@bot.on_message(filters.command("play"))
async def play_command(_, message):
    if not await require_channel_membership(message):
        return
    if len(message.command) < 2:
        return await message.reply_text("Usage: /play <song name>")
    query = " ".join(message.command[1:]).strip()
    status = await message.reply_text(f"🔎 Finding tracks for **{query}**...")
    try:
        results = await songsx_source.search(query, account_manager.clients)
        results = results[:7]
        if not results:
            return await status.edit_text("Songsx returned no song results. Try another title or artist.")
        requester = message.from_user
        requester_name = " ".join(part for part in [requester.first_name, requester.last_name] if part) if requester else "Telegram user"
        requester_username = f"@{requester.username}" if requester and requester.username else ""
        for item in results:
            item["requested_by"] = requester_name
            item["requested_by_username"] = requester_username
        if len(results) == 1:
            selected = results[0]
        else:
            await edit_song_choices(message.chat.id, status.id, results)
            search_cache[message.chat.id] = {str(x["id"]): x for x in results}
            return
        queues[message.chat.id].append(selected)
        if message.chat.id not in players:
            await status.edit_text("⏳ Fetching audio and preparing the voice chat...")
            await play_next(message.chat.id, status.id)
        else:
            await status.edit_text(
                f"➕ Added to queue: **{selected.get('name')}** — {selected.get('artist_name')}"
            )
    except Exception as exc:
        log.exception("Songsx search/play failed")
        await status.edit_text(f"❌ Couldn't search Songsx or play that track: {type(exc).__name__}: {exc}")


search_cache = defaultdict(dict)


@bot.on_callback_query(filters.regex(r"^choose:([A-Za-z0-9_-]+)$"))
async def choose_track(_, query):
    if not await is_channel_member(query.from_user.id):
        await query.answer("Join @xyronschannel and confirm membership before selecting tracks.", show_alert=True)
        try:
            await send_subscription_prompt(query.from_user.id)
        except Exception:
            pass
        return
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


@bot.on_callback_query(filters.regex(r"^player:(replay|pause|resume|skip|stop|queue|vol_down|vol_up)$"))
async def player_controls(_, query):
    if not await is_channel_member(query.from_user.id):
        await query.answer("Join @xyronschannel before using music controls.", show_alert=True)
        try:
            await send_subscription_prompt(query.from_user.id)
        except Exception:
            pass
        return
    chat_id = query.message.chat.id
    action = query.matches[0].group(1)
    if not await can_control(query.message.chat, query.from_user):
        return await query.answer("Only group admins or the bot owner can control playback.", show_alert=True)
    try:
        if action == "queue":
            upcoming = list(queues[chat_id])
            if not upcoming:
                await query.answer("The queue is empty.", show_alert=True)
            else:
                lines = [f"{i}. {item.get('name', 'Unknown')} — {item.get('artist_name', 'Unknown artist')}" for i, item in enumerate(upcoming[:15], 1)]
                await query.answer("Queue sent")
                await bot.send_message(chat_id, "📜 XYRON MUSIC QUEUE\n" + "\n".join(lines))
            return
        if action == "replay":
            track = players.get(chat_id)
            if not track:
                return await query.answer("Nothing is playing.", show_alert=True)
            await calls.leave_call(chat_id)
            await ensure_player_in_group(chat_id)
            await calls.play(chat_id, track["path"])
            track["started_at"] = time.monotonic()
            track["paused_elapsed"] = 0
            track["paused"] = False
            await query.answer("Playing from the beginning")
        elif action == "pause":
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
            await edit_player_card(chat_id, query.message.id, "⏹ <b>Playback stopped.</b> Queue cleared.", with_keyboard=False)
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
        await edit_player_card(chat_id, query.message.id, render_player(chat_id))
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
    if not await require_channel_membership(message):
        return
    chat_id = message.chat.id
    upcoming = list(queues[chat_id])
    if not upcoming:
        return await message.reply_text("📭 The queue is empty.")
    lines = [f"{i}. {x.get('name')} — {x.get('artist_name')}" for i, x in enumerate(upcoming[:15], 1)]
    await message.reply_text("📜 **UP NEXT**\n" + "\n".join(lines))


@bot.on_message(filters.command("np"))
async def np_command(_, message):
    if not await require_channel_membership(message):
        return
    chat_id = message.chat.id
    if chat_id not in players:
        return await message.reply_text("🎵 Nothing is playing.")
    track = players[chat_id]
    cover = make_player_cover(
        title=track.get("title", "Unknown title"),
        artist=track.get("artist", "Unknown artist"),
        requested_by=track.get("requested_by", "A group member"),
        bot_username=PUBLIC_BOT_USERNAME,
    )
    await send_player_card(chat_id, cover, render_player(chat_id))


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
        global PUBLIC_BOT_USERNAME
        PUBLIC_BOT_USERNAME = me.username or PUBLIC_BOT_USERNAME
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
