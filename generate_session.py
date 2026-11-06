import asyncio
import os

from dotenv import load_dotenv
from pyrogram import Client

load_dotenv()

API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")


async def main():
    if not API_ID or not API_HASH:
        raise SystemExit("Set API_ID and API_HASH in your environment first.")

    async with Client("music_session", api_id=API_ID, api_hash=API_HASH) as app:
        session = await app.export_session_string()
        print("\nYour SESSION_STRING (keep it private):\n")
        print(session)


if __name__ == "__main__":
    asyncio.run(main())
