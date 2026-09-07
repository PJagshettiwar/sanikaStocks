import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_API_ID = int(os.environ["TELEGRAM_API_ID"])
TELEGRAM_API_HASH = os.environ["TELEGRAM_API_HASH"]
TELEGRAM_SESSION_NAME = os.getenv("TELEGRAM_SESSION_NAME", "data/stock_agent")
WATCHED_CHANNELS = [int(c.strip()) for c in os.environ["WATCHED_CHANNELS"].split(",")]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
APPROVAL_CHAT_ID = int(os.environ["APPROVAL_CHAT_ID"])

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openrouter")

if LLM_PROVIDER == "gemini":
    LLM_API_KEY = os.environ["GEMINI_API_KEY"]
    LLM_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
    TIER1_MODEL = os.getenv("TIER1_MODEL", "gemini-2.0-flash")
    TIER2_MODEL = os.getenv("TIER2_MODEL", "gemini-2.0-flash")
elif LLM_PROVIDER == "openrouter":
    LLM_API_KEY = os.environ["OPENROUTER_API_KEY"]
    LLM_BASE_URL = "https://openrouter.ai/api/v1"
    TIER1_MODEL = os.getenv("TIER1_MODEL", "nvidia/nemotron-3.5-lightning:free")
    TIER2_MODEL = os.getenv("TIER2_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")
else:
    raise ValueError(f"LLM_PROVIDER must be 'openrouter' or 'gemini', got {LLM_PROVIDER!r}")

INDSTOCKS_CLIENT_ID = os.environ["INDSTOCKS_CLIENT_ID"]
INDSTOCKS_TOTP_SECRET = os.environ["INDSTOCKS_TOTP_SECRET"]
INDSTOCKS_MPIN = os.environ["INDSTOCKS_MPIN"]

DEFAULT_STOP_LOSS_PCT = float(os.getenv("DEFAULT_STOP_LOSS_PCT", "15"))
DEFAULT_ALLOCATION_PCT = float(os.getenv("DEFAULT_ALLOCATION_PCT", "10"))
FIXED_ALLOCATION_AMOUNT = float(os.getenv("FIXED_ALLOCATION_AMOUNT", "5000"))
MAX_SIGNAL_AGE_MINUTES = int(os.getenv("MAX_SIGNAL_AGE_MINUTES", "60"))
POLL_INTERVAL_MINUTES = int(os.getenv("POLL_INTERVAL_MINUTES", "10"))
MAX_DAILY_TRADES = int(os.getenv("MAX_DAILY_TRADES", "5"))
