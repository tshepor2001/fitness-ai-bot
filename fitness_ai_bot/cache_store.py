"""SQLite-backed cache for MCP tool responses with TTL expiry."""

import json
import logging
import time

import aiosqlite

from fitness_ai_bot import config

logger = logging.getLogger(__name__)

DB_PATH = config.DATA_DIR / "cache.db"


class CacheStore:
    """Persists MCP tool responses keyed by (user_id, tool_name, args)."""

    def __init__(self) -> None:
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(DB_PATH)
        await self._db.execute(
            """CREATE TABLE IF NOT EXISTS tool_cache (
                   user_id    INTEGER NOT NULL,
                   tool_name  TEXT    NOT NULL,
                   args_json  TEXT    NOT NULL,
                   response   TEXT    NOT NULL,
                   source_tag TEXT    NOT NULL DEFAULT '',
                   fetched_at REAL   NOT NULL,
                   PRIMARY KEY (user_id, tool_name, args_json)
               )"""
        )
        await self._db.commit()
        logger.info("Cache store ready (%s)", DB_PATH)

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    @staticmethod
    def _args_key(args: dict) -> str:
        """Deterministic JSON string for cache key."""
        return json.dumps(args, sort_keys=True, default=str)

    async def get(
        self, user_id: int, tool_name: str, args: dict, ttl: float,
    ) -> str | None:
        """Return cached response if it exists and is younger than *ttl* seconds."""
        key = self._args_key(args)
        cutoff = time.time() - ttl
        async with self._db.execute(
            "SELECT response FROM tool_cache "
            "WHERE user_id = ? AND tool_name = ? AND args_json = ? AND fetched_at > ?",
            (user_id, tool_name, key, cutoff),
        ) as cur:
            row = await cur.fetchone()
        return row[0] if row else None

    async def put(
        self,
        user_id: int,
        tool_name: str,
        args: dict,
        response: str,
        source_tag: str = "",
    ) -> None:
        """Insert or replace a cached tool response."""
        key = self._args_key(args)
        await self._db.execute(
            "INSERT OR REPLACE INTO tool_cache "
            "(user_id, tool_name, args_json, response, source_tag, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, tool_name, key, response, source_tag, time.time()),
        )
        await self._db.commit()

    async def get_all_fresh(
        self, user_id: int, ttl: float,
    ) -> list[dict]:
        """Return all fresh cache rows for a user."""
        cutoff = time.time() - ttl
        async with self._db.execute(
            "SELECT tool_name, response, source_tag, fetched_at FROM tool_cache "
            "WHERE user_id = ? AND fetched_at > ? ORDER BY tool_name",
            (user_id, cutoff),
        ) as cur:
            rows = await cur.fetchall()
        return [
            {"tool_name": r[0], "response": r[1], "source_tag": r[2], "fetched_at": r[3]}
            for r in rows
        ]

    async def evict_user(self, user_id: int) -> None:
        """Delete all cached data for a user."""
        await self._db.execute(
            "DELETE FROM tool_cache WHERE user_id = ?", (user_id,)
        )
        await self._db.commit()

    async def purge_expired(self, ttl: float) -> int:
        """Delete all rows older than *ttl* seconds. Returns count."""
        cutoff = time.time() - ttl
        cur = await self._db.execute(
            "DELETE FROM tool_cache WHERE fetched_at <= ?", (cutoff,)
        )
        await self._db.commit()
        return cur.rowcount
