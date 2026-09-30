"""
seed.py — Create the first manager account.

Run once before starting the app:
    python seed.py

You will be prompted for a username, name, and password.
"""
import asyncio
import os
import sys
from dotenv import load_dotenv

load_dotenv()

MONGO_URI     = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB_NAME = os.environ.get("MONGO_DB_NAME", "attendance_web")


async def main():
    try:
        import bcrypt as _bcrypt
        from motor.motor_asyncio import AsyncIOMotorClient
    except ImportError:
        print("ERROR: Dependencies not installed. Run:  pip install -r requirements.txt")
        sys.exit(1)

    print("=" * 50)
    print("  Attendance Tracker — First Manager Setup")
    print("=" * 50)
    print()

    username = input("Manager username (e.g. admin): ").strip()
    name     = input("Full name (e.g. Thrisha Poojary): ").strip()
    password = input("Password: ").strip()

    if not username or not name or not password:
        print("ERROR: All fields are required.")
        sys.exit(1)

    hashed = _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode()

    client = AsyncIOMotorClient(MONGO_URI)
    db     = client[MONGO_DB_NAME]

    existing = await db["users"].find_one({"username": username})
    if existing:
        # update the hash so it is always a fresh bcrypt hash
        await db["users"].update_one(
            {"username": username},
            {"$set": {"password_hash": hashed}},
        )
        print(f"\nUser '{username}' already exists — password updated.")
        client.close()
        return

    await db["users"].create_index("username", unique=True)
    await db["users"].insert_one({
        "username": username,
        "name": name,
        "password_hash": hashed,
        "role": "manager",
    })

    client.close()
    print()
    print("Manager account created!")
    print(f"  Username : {username}")
    print(f"  Name     : {name}")
    print(f"  Role     : manager")
    print()
    print("You can now start the app and log in at http://localhost:8000")


if __name__ == "__main__":
    asyncio.run(main())
