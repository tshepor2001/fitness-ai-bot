"""AI agent — Claude tool-use loop over per-user MCP tools."""

import asyncio
import json
import logging
from typing import Any

import anthropic

from fitness_ai_bot import config
from fitness_ai_bot.cache_store import CacheStore
from fitness_ai_bot.mcp_client import _UserSession

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a knowledgeable fitness and endurance-sports assistant.
You have access to the user's Garmin Connect and TrainingPeaks data via tools.

Guidelines:
- Use tools to fetch real data before answering — never fabricate metrics.
- When the user asks about recent activity, default to the last 7 days unless specified.
- Present numbers clearly with units (km, bpm, watts, TSS, etc.).
- Give concise, actionable insights. Avoid filler.
- If a tool call fails, tell the user honestly and suggest alternatives.
"""

MAX_TOOL_ROUNDS = 10  # safety cap on iterative tool calls
_RATE_LIMIT_RETRIES = 3

# Only expose tools the agent actually needs — sending all 120 tool schemas
# consumes ~20k input tokens per call, blowing past rate limits.
_ALLOWED_TOOLS: set[str] = {
    # ── Garmin ──────────────────────────────────────────────────
    "get_activities",
    "get_activity",
    "get_steps_data",
    "get_daily_steps",
    "get_heart_rates",
    "get_sleep_data",
    "get_body_composition",
    "get_stats",
    "get_training_readiness",
    "get_training_status",
    "get_user_summary",
    "get_body_battery",
    "get_stress_data",
    "get_hrv_data",
    "get_floors",
    "get_respiration_data",
    "get_race_predictions",
    "get_personal_record",
    "get_endurance_score",
    "get_hill_score",
    "get_current_date",
    # ── TrainingPeaks ───────────────────────────────────────────
    "get_user",
    "get_current_fitness",
    "get_workouts",
    "get_fitness_data",
    "get_strength_workouts",
    "get_workout",
    "get_workout_details",
    "search_workouts",
    "get_best_power",
    "get_peaks",
}

# These TP tools require a paid subscription.  Excluded at runtime for
# free-tier users to avoid "Payment Required" errors.
_TP_PREMIUM_ONLY: set[str] = {"get_fitness_data", "get_current_fitness"}


async def _create_with_retry(
    client: anthropic.AsyncAnthropic,
    *,
    system: str,
    tools: list[dict[str, Any]],
    messages: list[dict[str, Any]],
) -> anthropic.types.Message:
    """Call messages.create with exponential backoff on 429 rate-limit errors."""
    for attempt in range(_RATE_LIMIT_RETRIES + 1):
        try:
            return await client.messages.create(
                model=config.MODEL,
                max_tokens=4096,
                system=system,
                tools=tools,
                messages=messages,
            )
        except anthropic.RateLimitError:
            if attempt == _RATE_LIMIT_RETRIES:
                raise
            wait = 2 ** attempt * 15  # 15s, 30s, 60s
            logger.warning("Rate limited (429), retrying in %ds …", wait)
            await asyncio.sleep(wait)
    raise RuntimeError("unreachable")


def _is_premium(raw: str) -> bool:
    """Return True if the TP get_user response indicates a premium account."""
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return False
    if isinstance(data, dict):
        if data.get("isPremium"):
            return True
        acct = str(data.get("accountType", "")).lower()
        if acct in ("premium", "paid"):
            return True
    return False


async def _check_tp_premium(
    session: _UserSession,
    cache_store: CacheStore | None = None,
    user_id: int = 0,
) -> bool:
    """Determine TP premium status, preferring cached get_user data."""
    # 1. Try the cache first (populated by DataCache.sync).
    if cache_store and user_id:
        try:
            cached = await cache_store.get(user_id, "get_user", {}, config.CACHE_TTL)
            if cached is not None:
                result = _is_premium(cached)
                logger.info("TP premium (from cache): %s", result)
                return result
        except Exception as exc:
            logger.debug("Cache lookup for get_user failed: %s", exc)

    # 2. Fall back to a live MCP call.
    try:
        raw = await session.call_tool("get_user", {})
        result = _is_premium(raw)
        logger.info("TP premium (from MCP call): %s", result)
        return result
    except Exception as exc:
        logger.warning("Could not determine TP premium status: %s", exc)
        return False


async def ask(
    question: str,
    session: _UserSession,
    cached_context: str = "",
    cache_store: CacheStore | None = None,
    user_id: int = 0,
) -> str:
    """Send a question through Claude using the user's MCP session.

    If *cached_context* is provided it is appended to the system prompt
    so the LLM can answer common questions in one round without tool calls.
    """
    client = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)

    # Check TP premium status once per session, then cache on the session.
    allowed = _ALLOWED_TOOLS
    if not getattr(session, "_tp_premium_checked", False):
        session._tp_premium_checked = True
        session._tp_premium = await _check_tp_premium(session, cache_store, user_id)
    if not getattr(session, "_tp_premium", False):
        allowed = allowed - _TP_PREMIUM_ONLY

    all_tools = session.get_tools()
    tools = [t for t in all_tools if t["name"] in allowed]
    logger.info("Tools: %d/%d sent to LLM (tp_premium=%s)", len(tools), len(all_tools), getattr(session, "_tp_premium", None))

    system = SYSTEM_PROMPT
    if cached_context:
        system += (
            "\n\nThe following fitness data has already been fetched for this user. "
            "Use it to answer directly whenever possible — only call tools if the "
            "question requires data not present below.\n\n" + cached_context
        )

    messages: list[dict[str, Any]] = [{"role": "user", "content": question}]

    for _ in range(MAX_TOOL_ROUNDS):
        response = await _create_with_retry(
            client, system=system, tools=tools, messages=messages,
        )

        u = response.usage
        logger.info(
            "Token usage | input: %d | output: %d | cache_creation: %s | cache_read: %s",
            u.input_tokens,
            u.output_tokens,
            getattr(u, "cache_creation_input_tokens", None),
            getattr(u, "cache_read_input_tokens", None),
        )

        # collect any tool-use blocks
        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

        if not tool_use_blocks:
            # final text answer
            return _extract_text(response)

        # append assistant turn
        messages.append({"role": "assistant", "content": response.content})

        # execute every tool call and build tool_result blocks
        tool_results = []
        for block in tool_use_blocks:
            logger.info("Tool call: %s(%s)", block.name, json.dumps(block.input)[:200])
            try:
                result_text = await session.call_tool(block.name, block.input)
            except Exception as exc:
                result_text = f"Tool error: {exc}"
                logger.exception("Tool call failed: %s", block.name)

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result_text,
            })

        messages.append({"role": "user", "content": tool_results})

    return _extract_text(response)


def _extract_text(response: anthropic.types.Message) -> str:
    parts = [b.text for b in response.content if hasattr(b, "text")]
    return "\n".join(parts) or "(no response)"
