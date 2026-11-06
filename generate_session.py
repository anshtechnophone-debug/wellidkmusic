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
    try:
        count = int(input("How many simultaneous groups should the player support? [3]: ").strip() or "3")
    except ValueError:
        raise SystemExit("Enter a whole number.")
    if not 1 <= count <= 10:
        raise SystemExit("Choose between 1 and 10 separate sessions.")

    sessions = []
    for index in range(1, count + 1):
        print(f"\nAuthorize player session {index}/{count}. Use the same dedicated Telegram account each time.")
        async with Client(
            f"music_player_session_{index}",
            api_id=API_ID,
            api_hash=API_HASH,
            in_memory=True,
        ) as app:
            sessions.append(await app.export_session_string())

    print("\nAdd this line to your .env file (keep it private):\n")
    print('SESSION_STRINGS="' + ",".join(sessions) + '"')
    print("\nEach entry is a separate login session. Never share or upload this value.")


if __name__ == "__main__":
    asyncio.run(main())
