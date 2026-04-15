"""Per-user fitness data cache — fetches and stores Garmin/TP data locally.

On connect (and periodically), we pull the most commonly needed data
via MCP tool calls and store the responses in a SQLite database.
Cached entries have a configurable TTL (default 2 hours).  If a
response is still fresh, the MCP tool is not called again.

The cached snapshot is injected into the system prompt so the LLM
can answer most questions in a single round without any tool calls.
"""

import asyncio
import logging
import time
from datetime import date, timedelta
from typing import Any

from fitness_ai_bot.mcp_client import _UserSession
from fitness_ai_bot.cache_store import CacheStore
from fitness_ai_bot import config

logger = logging.getLogger(__name__)


def _today() -> str:
    return date.today().isoformat()


def _days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


# Tools to call on each sync: (tool_name, source_tag, kwargs_factory)
# kwargs_factory is either a static dict or a callable returning a dict
# (callable is used for date-dependent args resolved at sync time).
_TODAY_DATE: dict[str, Any] = {}  # sentinel — replaced at sync time


def _garmin_today() -> dict[str, Any]:
    return {"date": _today()}


_SYNC_PLAN: list[tuple[str, str, dict[str, Any] | None]] = [
    # ── Garmin ───────────────────────────────────────────────────────
    ("get_activities", "garmin", {"start": 0, "limit": 10}),
    ("get_steps_data", "garmin", _TODAY_DATE),
    ("get_heart_rates", "garmin", _TODAY_DATE),
    ("get_sleep_data", "garmin", _TODAY_DATE),
    ("get_body_composition", "garmin", _TODAY_DATE),
    ("get_stats", "garmin", _TODAY_DATE),
    ("get_training_readiness", "garmin", _TODAY_DATE),
    ("get_training_status", "garmin", _TODAY_DATE),
    ("get_user_summary", "garmin", _TODAY_DATE),
    # ── TrainingPeaks ────────────────────────────────────────────────
    ("get_user", "trainingpeaks", None),
    ("get_current_fitness", "trainingpeaks", None),
]

# TrainingPeaks tools that need date args, resolved at sync time.
_TP_DATE_TOOLS: list[tuple[str, int]] = [
    ("get_workouts", 30),         # last 30 days of workouts
    ("get_fitness_data", 30),     # CTL/ATL/TSB for last 30 days
    ("get_strength_workouts", 30),
]


def _tool_label(tool_name: str) -> str:
    return tool_name.replace("get_", "").replace("_", " ").title()


def _how_long_ago_wall(ts: float) -> str:
    """Human-friendly delta from a wall-clock timestamp (time.time())."""
    delta = int(time.time() - ts)
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{delta // 60}m ago"
    return f"{delta // 3600}h ago"


class DataCache:
    """Manages per-user cached fitness data with SQLite persistence."""

    def __init__(self) -> None:
        self._store = CacheStore()

    async def open(self) -> None:
        await self._store.open()

    async def close(self) -> None:
        await self._store.close()

    async def sync(self, user_id: int, session: _UserSession) -> None:
        """Fetch data from MCP tools, skipping tools whose cache is still fresh."""
        ttl = config.CACHE_TTL
        available_tools = {t["name"] for t in session.get_tools()}
        fetched = 0
        cached_hits = 0

        # Static-arg tools (Garmin + simple TP)
        for tool_name, source, kwargs in _SYNC_PLAN:
            if tool_name not in available_tools:
                continue
            if kwargs is _TODAY_DATE:
                if tool_name == "get_body_composition":
                    resolved = {"start_date": _today()}
                else:
                    resolved = {"date": _today()}
            else:
                resolved = kwargs or {}

            hit = await self._store.get(user_id, tool_name, resolved, ttl)
            if hit is not None:
                cached_hits += 1
                continue

            ok = await self._fetch_and_store(
                user_id, session, tool_name, resolved, source,
            )
            if ok:
                fetched += 1

        # Date-range TP tools
        for tool_name, lookback_days in _TP_DATE_TOOLS:
            if tool_name not in available_tools:
                continue
            args = {"startDate": _days_ago(lookback_days), "endDate": _today()}

            hit = await self._store.get(user_id, tool_name, args, ttl)
            if hit is not None:
                cached_hits += 1
                continue

            ok = await self._fetch_and_store(
                user_id, session, tool_name, args, "trainingpeaks",
            )
            if ok:
                fetched += 1

        logger.info(
            "Cache sync for user %d: %d fresh from DB, %d fetched from MCP",
            user_id, cached_hits, fetched,
        )

    def get_sources(self, user_id: int) -> list[str]:
        """Synchronous wrapper — reads from in-memory snapshot built by get_context/sync."""
        # We'll compute sources from get_all_fresh in the async path;
        # this is kept for back-compat but callers should prefer async version.
        return self._last_sources.get(user_id, [])

    async def get_sources_async(self, user_id: int) -> list[str]:
        rows = await self._store.get_all_fresh(user_id, config.CACHE_TTL)
        return sorted({r["source_tag"] for r in rows if r["source_tag"]})

    _last_sources: dict[int, list[str]] = {}

    async def _fetch_and_store(
        self,
        user_id: int,
        session: _UserSession,
        tool_name: str,
        args: dict[str, Any],
        source_tag: str,
        *,
        retries: int = 2,
    ) -> bool:
        for attempt in range(1, retries + 1):
            try:
                result = await session.call_tool(tool_name, args, timeout=25.0)
                if not result or not result.strip():
                    return False
                lower = result.strip().lower()
                if lower.startswith("error") or "an error has occurred" in lower:
                    logger.warning(
                        "Cache sync [user %d]: %s returned error text: %s",
                        user_id, tool_name, result[:300],
                    )
                    return False
                await self._store.put(user_id, tool_name, args, result, source_tag)
                logger.info("Cache sync [user %d]: %s → %d chars (stored)", user_id, tool_name, len(result))
                return True
            except asyncio.TimeoutError:
                logger.warning(
                    "Cache sync [user %d]: %s timed out (attempt %d/%d)",
                    user_id, tool_name, attempt, retries,
                )
            except Exception:
                logger.warning(
                    "Cache sync [user %d]: %s failed (attempt %d/%d), skipping",
                    user_id, tool_name, attempt, retries, exc_info=True,
                )
                break
        return False

    async def get_context(self, user_id: int) -> str:
        """Build the context block from fresh DB rows for a user."""
        rows = await self._store.get_all_fresh(user_id, config.CACHE_TTL)
        if not rows:
            return ""

        sources: set[str] = set()
        oldest_ts = min(r["fetched_at"] for r in rows)
        parts = [f"=== Cached Fitness Data (fetched {_how_long_ago_wall(oldest_ts)}) ==="]
        for r in rows:
            label = _tool_label(r["tool_name"])
            data = r["response"]
            text = data if len(data) < 4000 else data[:4000] + "\n... (truncated)"
            parts.append(f"\n── {label} ──\n{text}")
            if r["source_tag"]:
                sources.add(r["source_tag"])

        # Stash sources for the sync get_sources() call
        self._last_sources[user_id] = sorted(sources)
        return "\n".join(parts)

    async def is_fresh(self, user_id: int) -> bool:
        """True if at least one cached row exists within TTL for this user."""
        rows = await self._store.get_all_fresh(user_id, config.CACHE_TTL)
        return len(rows) > 0

    async def evict(self, user_id: int) -> None:
        await self._store.evict_user(user_id)
