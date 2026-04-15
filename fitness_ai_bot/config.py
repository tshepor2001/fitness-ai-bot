"""Configuration loaded from environment variables."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(f"Missing required env var: {name}")
    return val


# ── required ─────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = _require("ANTHROPIC_API_KEY")
TELEGRAM_BOT_TOKEN = _require("TELEGRAM_BOT_TOKEN")
ENCRYPTION_KEY = _require("ENCRYPTION_KEY")  # Fernet key for credential encryption

# ── model ────────────────────────────────────────────────────────────
MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-20250414")

# ── data directory ───────────────────────────────────────────────────
DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))

# ── session pool ─────────────────────────────────────────────────────
SESSION_IDLE_TIMEOUT = int(os.getenv("SESSION_IDLE_TIMEOUT", "600"))  # seconds

# ── data cache ───────────────────────────────────────────────────────
CACHE_TTL = int(os.getenv("CACHE_TTL", "7200"))  # seconds (default 2 hours)

# ── Telegram: restrict to specific user IDs (comma-separated). Empty = allow all.
ALLOWED_USER_IDS = os.getenv("ALLOWED_USER_IDS", "")
