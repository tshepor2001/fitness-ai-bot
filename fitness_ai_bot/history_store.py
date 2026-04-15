"""Request history store backed by SQLite."""

import logging
import time

import aiosqlite

from fitness_ai_bot import config

logger = logging.getLogger(__name__)

DB_PATH = config.DATA_DIR / "history.db"


class HistoryStore:
    """Persists question/answer pairs per user."""

    def __init__(self) -> None:
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(DB_PATH)
        await self._db.execute(
            """CREATE TABLE IF NOT EXISTS history (
                   id        INTEGER PRIMARY KEY AUTOINCREMENT,
                   user_id   INTEGER NOT NULL,
                   question  TEXT    NOT NULL,
                   answer    TEXT    NOT NULL,
                   sources   TEXT    NOT NULL DEFAULT '[]',
                   timestamp REAL    NOT NULL
               )"""
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_user ON history (user_id, timestamp DESC)"
        )
        await self._db.commit()
        logger.info("History store ready (%s)", DB_PATH)

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    async def add(
        self,
        user_id: int,
        question: str,
        answer: str,
        sources: str = "[]",
    ) -> int:
        """Insert a history row and return its id."""
        cur = await self._db.execute(
            "INSERT INTO history (user_id, question, answer, sources, timestamp) VALUES (?, ?, ?, ?, ?)",
            (user_id, question, answer, sources, time.time()),
        )
        await self._db.commit()
        return cur.lastrowid

    async def list(
        self,
        user_id: int,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """Return recent history for a user, newest first."""
        async with self._db.execute(
            "SELECT id, question, answer, sources, timestamp FROM history "
            "WHERE user_id = ? ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            (user_id, limit, offset),
        ) as cur:
            rows = await cur.fetchall()
        return [
            {
                "id": r[0],
                "question": r[1],
                "answer": r[2],
                "sources": r[3],
                "timestamp": r[4],
            }
            for r in rows
        ]

    async def delete_user(self, user_id: int) -> int:
        """Delete all history for a user. Returns row count."""
        cur = await self._db.execute(
            "DELETE FROM history WHERE user_id = ?", (user_id,)
        )
        await self._db.commit()
        return cur.rowcount
