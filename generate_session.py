"""
generate_session.py — Run this ONCE on your local machine to create a
Telethon StringSession.  Paste the printed string as TELEGRAM_SESSION_STRING
in your Railway environment variables.  Never commit it to git.

Usage:
    pip install telethon python-dotenv
    python generate_session.py
"""
import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession
from dotenv import load_dotenv
import os

load_dotenv()

API_ID   = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")

if not API_ID or not API_HASH:
    raise SystemExit("Set TELEGRAM_API_ID and TELEGRAM_API_HASH in your .env first.")

async def main():
    async with TelegramClient(StringSession(), API_ID, API_HASH) as client:
        session_string = client.session.save()
        print("\n" + "=" * 60)
        print("YOUR SESSION STRING (add this to Railway env vars):")
        print("=" * 60)
        print(session_string)
        print("=" * 60 + "\n")
        print("Variable name:  TELEGRAM_SESSION_STRING")
        print("Keep this secret — it grants full access to your account.")

asyncio.run(main())
