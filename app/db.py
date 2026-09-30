from typing import Optional
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from app.config import MONGO_URI, MONGO_DB_NAME

_client: Optional[AsyncIOMotorClient] = None
_db: Optional[AsyncIOMotorDatabase] = None


def get_db() -> AsyncIOMotorDatabase:
    if _db is None:
        raise RuntimeError("Database not initialised.")
    return _db


async def init_db() -> None:
    global _client, _db
    _client = AsyncIOMotorClient(MONGO_URI)
    _db = _client[MONGO_DB_NAME]
    # users: username unique
    await _db["users"].create_index("username", unique=True)
    # attendance: one record per user per day
    await _db["attendance"].create_index([("user_id", 1), ("date", 1)], unique=True)
    # leave requests
    await _db["leave_requests"].create_index("request_id", unique=True)
    await _db["leave_requests"].create_index("user_id")


async def close_db() -> None:
    global _client
    if _client:
        _client.close()
        _client = None
