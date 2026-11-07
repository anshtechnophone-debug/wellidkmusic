import asyncio
import re

from telethon.errors import UserAlreadyParticipantError
from telethon.tl.functions.messages import ImportChatInviteRequest
from pytgcalls import PyTgCalls
from pytgcalls.types import AudioQuality, MediaStream


class MultiCallPool:
    """Assign one separately authorized MTProto session to each active group call."""

    def __init__(self, clients=None):
        self.clients = list(clients or [])
        self.instances = [PyTgCalls(client) for client in self.clients]
        self.chat_to_slot = {}
        self.slot_to_chat = {}
        self._allocation_lock = asyncio.Lock()
        self._client_lock = asyncio.Lock()
        self._update_handlers = []
        self._started = False
        self._handoff_handler = None

    def set_handoff_handler(self, handler):
        """Register a callback to clean up the previous group after account handoff."""
        self._handoff_handler = handler

    def _register_update_handler(self, instance, update_filter, handler):
        async def wrapped(_, update, _handler=handler):
            await _handler(self, update)

        instance.on_update(update_filter)(wrapped)

    def on_update(self, update_filter):
        def decorator(handler):
            self._update_handlers.append((update_filter, handler))
            for instance in self.instances:
                self._register_update_handler(instance, update_filter, handler)
            return handler

        return decorator

    async def add_client(self, client):
        """Add a newly authorized player account while the bot is running."""
        async with self._client_lock:
            if client in self.clients:
                return self.instances[self.clients.index(client)]

            instance = PyTgCalls(client)
            for update_filter, handler in self._update_handlers:
                self._register_update_handler(instance, update_filter, handler)

            if self._started:
                await instance.start()

            self.clients.append(client)
            self.instances.append(instance)
            return instance

    async def start(self):
        started = []
        try:
            for instance in self.instances:
                await instance.start()
                started.append(instance)
            self._started = True
        except Exception:
            for instance in reversed(started):
                try:
                    await instance.stop()
                except Exception:
                    pass
            raise

    async def stop(self):
        for instance in reversed(self.instances):
            try:
                await instance.stop()
            except Exception:
                pass
        self._started = False
        self.chat_to_slot.clear()
        self.slot_to_chat.clear()

    async def _instance_for(self, chat_id, allocate=False, preferred_client=None):
        slot = self.chat_to_slot.get(chat_id)
        if slot is not None:
            return self.instances[slot]
        if not allocate:
            raise RuntimeError("No player session is assigned to this chat.")

        async with self._allocation_lock:
            slot = self.chat_to_slot.get(chat_id)
            if slot is not None:
                return self.instances[slot]
            if preferred_client in self.clients:
                preferred_index = self.clients.index(preferred_client)
                if preferred_index not in self.slot_to_chat:
                    self.chat_to_slot[chat_id] = preferred_index
                    self.slot_to_chat[preferred_index] = chat_id
                    return self.instances[preferred_index]
            for index, instance in enumerate(self.instances):
                if index not in self.slot_to_chat:
                    self.chat_to_slot[chat_id] = index
                    self.slot_to_chat[index] = chat_id
                    return instance

            # Every account is already in another group's voice chat.
            # Move the first available account to the newly requested group.
            if self.instances:
                index = 0
                old_chat_id = self.slot_to_chat.get(index)
                if old_chat_id is not None:
                    try:
                        await self.instances[index].leave_call(old_chat_id)
                    except Exception:
                        pass
                    self.chat_to_slot.pop(old_chat_id, None)
                    self.slot_to_chat.pop(index, None)
                    if self._handoff_handler:
                        try:
                            await self._handoff_handler(old_chat_id, chat_id)
                        except Exception:
                            pass
                self.chat_to_slot[chat_id] = index
                self.slot_to_chat[index] = chat_id
                return self.instances[index]

        raise RuntimeError("No voice-player account is connected. Please contact the bot owner.")

    async def ensure_member(self, chat_id, invite_link, preferred_client=None):
        """Join the player session assigned to this chat using a bot-created invite."""
        await self._instance_for(chat_id, allocate=True, preferred_client=preferred_client)
        slot = self.chat_to_slot[chat_id]
        client = self.clients[slot]
        invite_hash = invite_link.rstrip("/").split("/")[-1].split("?")[0]
        invite_hash = invite_hash.lstrip("+")
        if not invite_hash or invite_hash.lower() == "joinchat":
            raise RuntimeError("Telegram returned an invalid group invite link.")
        me = await client.get_me()
        try:
            await client(ImportChatInviteRequest(invite_hash))
        except UserAlreadyParticipantError:
            pass
        # Confirm the account can now resolve the target chat before PyTgCalls joins.
        await client.get_entity(chat_id)
        return me

    async def play(self, chat_id, source):
        instance = await self._instance_for(chat_id, allocate=True)
        try:
            await instance.play(chat_id, MediaStream(source, AudioQuality.STUDIO))
        except Exception:
            await self.leave_call(chat_id)
            raise

    async def pause(self, chat_id):
        await (await self._instance_for(chat_id)).pause(chat_id)

    async def resume(self, chat_id):
        await (await self._instance_for(chat_id)).resume(chat_id)

    async def change_volume_call(self, chat_id, volume):
        await (await self._instance_for(chat_id)).change_volume_call(chat_id, volume)

    async def leave_call(self, chat_id):
        slot = self.chat_to_slot.get(chat_id)
        if slot is None:
            return
        try:
            await self.instances[slot].leave_call(chat_id)
        finally:
            self.chat_to_slot.pop(chat_id, None)
            self.slot_to_chat.pop(slot, None)
