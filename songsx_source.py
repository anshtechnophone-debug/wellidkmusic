import asyncio
import json
import logging
import re
import secrets
import unicodedata
from pathlib import Path

from telethon.tl.functions.messages import GetBotCallbackAnswerRequest
from telethon.tl.types import PeerChannel

log = logging.getLogger("wellidkmusic.songsx")


class SongsxSource:
    """Search the owner's Songsx bot through an authorized Telethon user session."""

    def __init__(self, bot_username, cache_channel_id, media_dir):
        self.bot_username = (bot_username or "Songsx_bot").lstrip("@")
        self.raw_cache_channel_id = int(cache_channel_id or 0)
        self.media_dir = Path(media_dir)
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.media_dir / "songsx_cache.json"
        self.index = self._load_index()
        self.pending = {}
        self._lock = asyncio.Lock()

    def _load_index(self):
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_index(self):
        temporary = self.index_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.index, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.index_path)

    @staticmethod
    def _key(value):
        value = unicodedata.normalize("NFKC", value).casefold()
        return re.sub(r"[^\\w]+", " ", value, flags=re.UNICODE).strip()

    @staticmethod
    def _is_audio(message):
        if getattr(message, "audio", None) or getattr(message, "voice", None):
            return True
        document = getattr(message, "document", None)
        mime = getattr(document, "mime_type", "") if document else ""
        return bool(mime and mime.lower().startswith("audio/"))

    async def _wait_for_keyboard(self, client, peer, after_id, timeout=25):
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            messages = await client.get_messages(peer, limit=15)
            for message in sorted(messages, key=lambda item: item.id):
                if message.id > after_id and message.buttons:
                    return message
            await asyncio.sleep(0.6)
        raise TimeoutError("Songsx did not return its song buttons in time.")

    async def search(self, query, clients):
        if not clients:
            raise RuntimeError("No connected Telegram user account. Use /newaccount first.")
        async with self._lock:
            key = self._key(query)
            cached = self.index.get(key)
            if cached and Path(cached.get("path", "")).is_file():
                return [{
                    "id": "cache_" + secrets.token_hex(4),
                    "name": cached.get("title", query),
                    "artist_name": cached.get("artist", "Unknown artist"),
                    "duration": cached.get("duration", 0),
                    "_source": "songsx",
                    "_cached_path": cached["path"],
                    "source_url": f"https://t.me/{self.bot_username}",
                }]

            client = clients[0]
            peer = await client.get_entity("@" + self.bot_username)
            sent = await client.send_message(peer, query)
            keyboard = await self._wait_for_keyboard(client, peer, sent.id)
            choices = []
            for row in keyboard.buttons or []:
                for button in row:
                    data = getattr(button, "data", None)
                    label = (getattr(button, "text", "") or "").strip()
                    if not data or not label:
                        continue
                    token = secrets.token_hex(4)
                    if " — " in label:
                        title, artist = label.split(" — ", 1)
                    elif " - " in label:
                        title, artist = label.split(" - ", 1)
                    else:
                        title, artist = label, "Unknown artist"
                    self.pending[token] = {
                        "client": client,
                        "peer": peer,
                        "message_id": keyboard.id,
                        "button_data": data,
                        "query": query,
                        "title": title.strip(),
                        "artist": artist.strip(),
                    }
                    choices.append({
                        "id": token,
                        "name": title.strip(),
                        "artist_name": artist.strip(),
                        "duration": 0,
                        "_source": "songsx",
                        "source_url": f"https://t.me/{self.bot_username}",
                    })
                    if len(choices) >= 7:
                        break
                if len(choices) >= 7:
                    break
            if not choices:
                raise RuntimeError("Songsx replied, but no selectable song buttons were found.")
            return choices

    async def _cache_peer(self, client):
        if not self.raw_cache_channel_id:
            raise RuntimeError("AUDIO_CACHE_CHANNEL_ID is not configured.")
        raw_id = abs(self.raw_cache_channel_id)
        marked_id = self.raw_cache_channel_id
        if marked_id > 0:
            marked_id = int("-100" + str(raw_id))
        try:
            return await client.get_input_entity(marked_id)
        except Exception:
            dialogs = await client.get_dialogs()
            for dialog in dialogs:
                if getattr(dialog.entity, "id", None) == raw_id:
                    return await client.get_input_entity(dialog.entity)
            return await client.get_input_entity(PeerChannel(raw_id))

    async def download_selected(self, track):
        cached_path = track.get("_cached_path")
        if cached_path and Path(cached_path).is_file():
            return str(cached_path)

        token = str(track.get("id", ""))
        context = self.pending.get(token)
        if not context:
            raise RuntimeError("This Songsx selection expired. Run /play again.")

        async with self._lock:
            client = context["client"]
            peer = context["peer"]
            baseline = await client.get_messages(peer, limit=1)
            baseline_id = baseline[0].id if baseline else context["message_id"]

            await client(GetBotCallbackAnswerRequest(
                peer=peer,
                msg_id=context["message_id"],
                data=context["button_data"],
            ))

            deadline = asyncio.get_running_loop().time() + 35
            audio_message = None
            while asyncio.get_running_loop().time() < deadline:
                selected_message = await client.get_messages(peer, ids=context["message_id"])
                if selected_message and self._is_audio(selected_message):
                    audio_message = selected_message
                    break
                recent = await client.get_messages(peer, limit=20)
                audio_message = next(
                    (item for item in sorted(recent, key=lambda item: item.id)
                     if item.id > baseline_id and self._is_audio(item)),
                    None,
                )
                if audio_message:
                    break
                await asyncio.sleep(0.7)

            if not audio_message:
                raise TimeoutError("Songsx did not send an audio file after the selection.")

            file_info = getattr(audio_message, "file", None)
            size = getattr(file_info, "size", 0) or 0
            if size and size > 100 * 1024 * 1024:
                raise RuntimeError("The selected audio file is larger than the 100 MB cache limit.")

            ext = getattr(file_info, "ext", None) or ".mp3"
            safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", context["title"])[:55] or "track"
            destination = self.media_dir / f"songsx_{audio_message.id}_{safe_name}{ext}"
            temporary = destination.with_suffix(destination.suffix + ".part")
            temporary.unlink(missing_ok=True)
            downloaded = await client.download_media(audio_message, file=str(temporary))
            if not downloaded or not temporary.exists() or temporary.stat().st_size < 10_000:
                temporary.unlink(missing_ok=True)
                raise RuntimeError("Songsx audio download was incomplete.")
            temporary.replace(destination)

            duration = 0
            audio = getattr(audio_message, "audio", None)
            if audio:
                duration = int(getattr(audio, "duration", 0) or 0)

            cache_peer = await self._cache_peer(client)
            caption = (
                f"{context['title']} — {context['artist']}\n"
                f"Search: {context['query']}\n"
                f"Source: @{self.bot_username}"
            )
            cached_message = await client.send_file(
                cache_peer,
                str(destination),
                caption=caption[:900],
                force_document=False,
            )

            self.index[self._key(context["query"])] = {
                "path": str(destination),
                "title": context["title"],
                "artist": context["artist"],
                "duration": duration,
                "cache_message_id": cached_message.id,
            }
            self._save_index()
            self.pending.pop(token, None)
            track["duration"] = duration
            track["name"] = context["title"]
            track["artist_name"] = context["artist"]
            return str(destination)
