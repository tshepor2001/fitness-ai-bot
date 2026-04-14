"""Encrypted credential store backed by SQLite."""

import json
import logging

import aiosqlite
from cryptography.fernet import Fernet

from fitness_ai_bot import config

logger = logging.getLogger(__name__)

DB_PATH = config.DATA_DIR / "credentials.db"


class CredentialStore:
    """Stores per-user Garmin + TrainingPeaks credentials with Fernet encryption."""

    def __init__(self) -> None:
        self._fernet = Fernet(config.ENCRYPTION_KEY.encode())
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(DB_PATH)
        await self._db.execute(
            """CREATE TABLE IF NOT EXISTS user_creds (
                   user_id INTEGER PRIMARY KEY,
                   data    BLOB NOT NULL
               )"""
        )
        await self._db.commit()
        logger.info("Credential store ready (%s)", DB_PATH)

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    # ── read / write ─────────────────────────────────────────────────

    async def save(self, user_id: int, creds: dict[str, str]) -> None:
        """Encrypt and persist credentials for a user."""
        blob = self._fernet.encrypt(json.dumps(creds).encode())
        await self._db.execute(
            "INSERT OR REPLACE INTO user_creds (user_id, data) VALUES (?, ?)",
            (user_id, blob),
        )
        await self._db.commit()

    async def load(self, user_id: int) -> dict[str, str] | None:
        """Load and decrypt credentials, or return None."""
        async with self._db.execute(
            "SELECT data FROM user_creds WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return json.loads(self._fernet.decrypt(row[0]).decode())

    async def delete(self, user_id: int) -> bool:
        """Remove a user's credentials. Returns True if they existed."""
        cur = await self._db.execute(
            "DELETE FROM user_creds WHERE user_id = ?", (user_id,)
        )
        await self._db.commit()
        return cur.rowcount > 0

    async def has_credentials(self, user_id: int) -> bool:
        async with self._db.execute(
            "SELECT 1 FROM user_creds WHERE user_id = ?", (user_id,)
        ) as cur:
            return (await cur.fetchone()) is not None
