import asyncio
import io
import json
import logging
import os
from pathlib import Path

import qrcode
from cryptography.fernet import Fernet, InvalidToken
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession

log = logging.getLogger("wellidkmusic.accounts")


class AccountManager:
    """Owner-only QR login and encrypted storage for voice-player accounts."""

    def __init__(self, bot, call_pool, api_id, api_hash, owner_id, data_dir):
        self.bot = bot
        self.call_pool = call_pool
        self.api_id = int(api_id)
        self.api_hash = api_hash
        self.owner_id = int(owner_id) if owner_id else 0
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.data_dir.chmod(0o700)
        except OSError:
            pass
        self.store_path = self.data_dir / "player_accounts.json"
        self.key_path = self.data_dir / ".session_key"
        self.fernet = self._load_cipher()
        self.clients = []
        self.accounts = {}
        self._login_lock = asyncio.Lock()

    def _load_cipher(self):
        if self.key_path.exists():
            key = self.key_path.read_bytes()
        else:
            key = Fernet.generate_key()
            descriptor = os.open(str(self.key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as key_file:
                key_file.write(key)
        try:
            self.key_path.chmod(0o600)
        except OSError:
            pass
        return Fernet(key)

    def _read_store(self):
        if not self.store_path.exists():
            return {}
        try:
            payload = json.loads(self.store_path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, json.JSONDecodeError):
            log.exception("Could not read player-account store")
            return {}

    def _write_store(self):
        temporary = self.store_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(self.accounts, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        temporary.replace(self.store_path)
        try:
            self.store_path.chmod(0o600)
        except OSError:
            pass

    def _is_owner(self, message):
        return bool(self.owner_id and message.from_user and message.from_user.id == self.owner_id)

    async def load_existing(self):
        stored = self._read_store()
        for account_id, record in stored.items():
            try:
                encrypted = record["session"].encode("ascii")
                session = self.fernet.decrypt(encrypted).decode("utf-8")
                client = TelegramClient(StringSession(session), self.api_id, self.api_hash)
                await client.start()
                await self.call_pool.add_client(client)
                self.clients.append(client)
                self.accounts[str(account_id)] = record
                log.info("Loaded saved voice-player account %s", account_id)
            except (InvalidToken, KeyError, ValueError):
                log.exception("Could not decrypt or load a saved player account")
            except Exception:
                log.exception("Could not start a saved player account %s", account_id)

    async def handle_new_account(self, _, message):
        if not self._is_owner(message):
            return await message.reply_text("⛔ This command is available only to the configured bot owner.")
        if message.chat.type != "private":
            return await message.reply_text("For account safety, use /newaccount in the bot's private chat.")
        if not self.owner_id:
            return await message.reply_text("Set OWNER_ID in .env to your numeric Telegram user ID, then restart the bot.")
        if len(self.accounts) >= 10:
            return await message.reply_text("The player-account limit is 10.")
        if self._login_lock.locked():
            return await message.reply_text("An account connection is already in progress.")
        async with self._login_lock:
            await self._run_qr_login(message)

    async def _run_qr_login(self, message):
        client = TelegramClient(StringSession(), self.api_id, self.api_hash)
        status = await message.reply_text(
            "🔐 **Connect a voice-player account**\n\n"
            "I'll send a temporary Telegram QR code here. Scan it using your own logged-in Telegram app: "
            "Settings → Devices → Link Desktop Device.\n\n"
            "Never forward the QR code. It authorizes access to your account."
        )
        try:
            await client.connect()
            qr = await client.qr_login()
            deadline = asyncio.get_running_loop().time() + 150
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                qr_image = qrcode.make(qr.url)
                image_buffer = io.BytesIO()
                image_buffer.name = "telegram-login.png"
                qr_image.save(image_buffer, format="PNG")
                image_buffer.seek(0)
                await self.bot.send_photo(
                    message.chat.id,
                    image_buffer,
                    caption="Scan this QR from Telegram → Settings → Devices → Link Desktop Device. "
                            "This code expires quickly; a refreshed code will be sent automatically if needed.",
                )
                try:
                    await qr.wait(timeout=min(25, remaining))
                    break
                except asyncio.TimeoutError:
                    await qr.recreate()

            me = await client.get_me()
            account_id = str(me.id)
            if account_id in self.accounts:
                await status.edit_text("That Telegram account is already connected.")
                return

            session_string = StringSession.save(client.session)
            encrypted = self.fernet.encrypt(session_string.encode("utf-8")).decode("ascii")
            record = {
                "username": me.username or "",
                "first_name": me.first_name or "",
                "session": encrypted,
            }
            await client.disconnect()

            player = TelegramClient(StringSession(session_string), self.api_id, self.api_hash)
            await player.start()
            try:
                await self.call_pool.add_client(player)
            except Exception:
                await player.disconnect()
                raise

            self.accounts[account_id] = record
            self.clients.append(player)
            try:
                self._write_store()
            except Exception:
                self.accounts.pop(account_id, None)
                self.clients.remove(player)
                await player.disconnect()
                raise

            display = f"@{me.username}" if me.username else (me.first_name or account_id)
            await status.edit_text(
                f"✅ **Player account connected successfully!**\n\n"
                f"Account: {display}\n"
                f"Telegram ID: `{account_id}`\n"
                f"Use /accounts to view connected players."
            )
        except SessionPasswordNeededError:
            await status.edit_text(
                "Telegram requires an additional two-step verification step for this account. "
                "For safety, this bot does not collect account passwords. The login was not saved."
            )
        except asyncio.TimeoutError:
            await status.edit_text("⌛ QR login timed out. Run /newaccount to try again.")
        except Exception as exc:
            log.exception("Player account QR login failed")
            await status.edit_text(f"❌ Could not connect the account: {type(exc).__name__}")
        finally:
            if client.is_connected():
                await client.disconnect()

    async def handle_accounts(self, _, message):
        if not self._is_owner(message):
            return await message.reply_text("⛔ Owner only.")
        if not self.accounts:
            return await message.reply_text("No player accounts connected yet. Use /newaccount.")
        rows = []
        for account_id, record in self.accounts.items():
            username = f"@{record['username']}" if record.get("username") else record.get("first_name", "Telegram user")
            rows.append(f"• {username} — `{account_id}`")
        await message.reply_text("🎧 **Connected player accounts**\n\n" + "\n".join(rows))
