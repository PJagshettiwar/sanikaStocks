import os

# config.py reads these at import time via os.environ. Set them before any test
# module imports config, so the suite doesn't depend on a real .env being present.
_DEFAULTS = {
    "TELEGRAM_API_ID": "12345",
    "TELEGRAM_API_HASH": "test_hash",
    "WATCHED_CHANNELS": "-1001234567890",
    "TELEGRAM_BOT_TOKEN": "test:token",
    "APPROVAL_CHAT_ID": "-1009876543210",
    "GEMINI_API_KEY": "test_gemini_key",
    "INDSTOCKS_CLIENT_ID": "test_client",
    "INDSTOCKS_TOTP_SECRET": "JBSWY3DPEHPK3PXP",
    "INDSTOCKS_MPIN": "1234",
}

for _key, _value in _DEFAULTS.items():
    os.environ.setdefault(_key, _value)
