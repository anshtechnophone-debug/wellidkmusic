import asyncio

from pytgcalls import PyTgCalls
from pytgcalls.types import AudioQuality, MediaStream


class MultiCallPool:
    """Assign one separately authorized MTProto session to each active group call."""

    def __init__(self, clients):
        if not clients:
            raise ValueError("At least one player client is required.")
        self.instances = [PyTgCalls(client) for client in clients]
        self.chat_to_slot = {}
        self.slot_to_chat = {}
        self._allocation_lock = asyncio.Lock()

    def on_update(self, update_filter):
        def decorator(handler):
            for instance in self.instances:
                async def wrapped(_, update, _handler=handler):
                    await _handler(self, update)
                instance.on_update(update_filter)(wrapped)
            return handler
        return decorator

    async def start(self):
        started = []
        try:
            for instance in self.instances:
                await instance.start()
                started.append(instance)
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
        self.chat_to_slot.clear()
        self.slot_to_chat.clear()

    async def _instance_for(self, chat_id, allocate=False):
        slot = self.chat_to_slot.get(chat_id)
        if slot is not None:
            return self.instances[slot]
        if not allocate:
            raise RuntimeError("No player session is assigned to this chat.")

        async with self._allocation_lock:
            slot = self.chat_to_slot.get(chat_id)
            if slot is not None:
                return self.instances[slot]
            for index, instance in enumerate(self.instances):
                if index not in self.slot_to_chat:
                    self.chat_to_slot[chat_id] = index
                    self.slot_to_chat[index] = chat_id
                    return instance

        raise RuntimeError(
            f"All {len(self.instances)} player session(s) are busy. "
            "Add more separate SESSION_STRINGS for more simultaneous groups."
        )

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
