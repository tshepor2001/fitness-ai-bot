"""AI agent — Claude tool-use loop over per-user MCP tools."""

import json
import logging
from typing import Any

import anthropic

from fitness_ai_bot import config
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


async def ask(question: str, session: _UserSession) -> str:
    """Send a question through Claude using the user's MCP session."""
    client = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
    tools = session.get_tools()

    messages: list[dict[str, Any]] = [{"role": "user", "content": question}]

    for _ in range(MAX_TOOL_ROUNDS):
        response = await client.messages.create(
            model=config.MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=tools,
            messages=messages,
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
