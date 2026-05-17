import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ALT_TOKENS = [t.strip() for t in os.getenv("ALT_TOKENS", "").split(",") if t.strip()]
GUILD_ID = int(os.getenv("GUILD_ID", "0")) if os.getenv("GUILD_ID") else None
OWNER_ID = int(os.getenv("OWNER_ID", "0")) if os.getenv("OWNER_ID") else None
PROXY_LIST = [p.strip() for p in os.getenv("PROXY_LIST", "").split(",") if p.strip()]
MAX_ALT_ACCOUNTS = int(os.getenv("MAX_ALT_ACCOUNTS", "5"))
MAX_ACCOUNTS_PER_USER = 3
BOOKWORK_CACHE_EXPIRY = 3600  # 1 hour in seconds
