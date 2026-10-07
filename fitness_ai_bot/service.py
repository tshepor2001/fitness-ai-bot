"""Reusable agent service wrapper around credentials, MCP sessions, and AI answering."""

import asyncio
import logging
import shutil
import json
import time

from fitness_ai_bot import config
from fitness_ai_bot.agent import ask
from fitness_ai_bot.credential_store import CredentialStore
from fitness_ai_bot.data_cache import DataCache
from fitness_ai_bot.history_store import HistoryStore
from fitness_ai_bot.mcp_client import MCPPool

logger = logging.getLogger(__name__)

HISTORY_TURNS = 6
HISTORY_WINDOW_SECONDS = 24 * 3600


class FitnessAgentService:
    """High-level interface for connecting users and answering questions."""

    def __init__(self) -> None:
        self._store = CredentialStore()
        self._pool = MCPPool(self._store)
        self._cache = DataCache()
        self._history = HistoryStore()
        self._sync_tasks: dict[int, asyncio.Task] = {}

    async def start(self) -> None:
        await self._store.open()
        await self._cache.open()
        await self._history.open()
        await self._pool.start()

    async def stop(self) -> None:
        await self._pool.stop()
        await self._history.close()
        await self._cache.close()
        await self._store.close()

    async def has_credentials(self, user_id: int) -> bool:
        return await self._store.has_credentials(user_id)

    async def connect_user(self, user_id: int, creds: dict[str, str], label: str = "") -> None:
        await self._store.save(user_id, creds, label=label)
        await self._pool.evict_user(user_id)
        # Clear stale Garmin tokens so the next session does a fresh auth
        token_dir = config.DATA_DIR / f"garmin_{creds['garmin_email']}"
        shutil.rmtree(token_dir, ignore_errors=True)

    async def disconnect_user(self, user_id: int) -> bool:
        await self._pool.evict_user(user_id)
        await self._cache.evict(user_id)
        return await self._store.delete(user_id)

    async def ask_user(self, user_id: int, question: str) -> tuple[str, list[str]]:
        session = await self._pool.get_session(user_id)
        if session is None:
            raise RuntimeError("User has no connected credentials")

        # Refresh a stale cache in the background; the agent can call tools directly meanwhile.
        if not await self._cache.is_fresh(user_id):
            self._start_background_sync(user_id, session)

        cutoff = time.time() - HISTORY_WINDOW_SECONDS
        recent = await self._history.list(user_id, limit=HISTORY_TURNS)
        history = [
            (r["question"], r["answer"])
            for r in reversed(recent)
            if r["timestamp"] >= cutoff
        ]

        answer = await ask(
            question, session,
            cache_store=self._cache._store, user_id=user_id,
            history=history,
        )
        sources = self._cache.get_sources(user_id)

        await self._history.add(
            user_id, question, answer, json.dumps(sources),
        )

        return answer, sources

    def _start_background_sync(self, user_id: int, session) -> None:
        running = self._sync_tasks.get(user_id)
        if running and not running.done():
            return

        async def _run() -> None:
            try:
                await self._cache.sync(user_id, session)
            except Exception:
                logger.warning("Background cache sync failed for user %d", user_id, exc_info=True)

        self._sync_tasks[user_id] = asyncio.create_task(_run())

    async def validate_user_session(self, user_id: int) -> None:
        """Warm up MCP sessions and fail fast if tools are unavailable."""
        session = await self._pool.get_session(user_id)
        if session is None:
            raise RuntimeError("User has no connected credentials")

    async def sync_cache(self, user_id: int) -> None:
        """Pre-fetch fitness data into the cache (called on connect)."""
        session = await self._pool.get_session(user_id)
        if session is None:
            return
        await self._cache.sync(user_id, session)

    async def get_sources(self, user_id: int) -> list[str]:
        """Return data source tags that have cached data for this user."""
        return await self._cache.get_sources_async(user_id)

    async def get_server_status(self, user_id: int) -> dict[str, str]:
        """Return per-server connection status (name → 'ok' | error)."""
        session = await self._pool.get_session(user_id)
        if session is None:
            return {}
        return getattr(session, "server_status", {})

    async def list_users(self) -> list[dict]:
        """Return all registered user labels."""
        return await self._store.list_users()

    async def update_user_label(self, user_id: int, label: str) -> None:
        await self._store.update_label(user_id, label)

    async def get_history(self, user_id: int, limit: int = 50, offset: int = 0) -> list[dict]:
        return await self._history.list(user_id, limit=limit, offset=offset)

    async def delete_history(self, user_id: int) -> int:
        return await self._history.delete_user(user_id)
