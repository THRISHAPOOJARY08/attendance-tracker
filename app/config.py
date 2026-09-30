import os
from dotenv import load_dotenv

load_dotenv()

MONGO_URI: str = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB_NAME: str = os.environ.get("MONGO_DB_NAME", "attendance_web")
COMPANY_TIMEZONE: str = os.environ.get("COMPANY_TIMEZONE", "Asia/Kolkata")
SECRET_KEY: str = os.environ.get("SECRET_KEY", "change-me")
ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 8  # 8-hour sessions
