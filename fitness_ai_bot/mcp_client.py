"""Per-user MCP session pool — spawns Garmin + TrainingPeaks servers on demand."""

import asyncio
import json
import logging
import os
import subprocess
import time
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from fitness_ai_bot import config
from fitness_ai_bot.credential_store import CredentialStore

logger = logging.getLogger(__name__)


def _passthrough_env() -> dict[str, str]:
    """Pass network/proxy-related env vars to MCP subprocesses."""
    keys = [
        "PATH",
        "HOME",
        "USER",
        "LANG",
        "LC_ALL",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "NODE_EXTRA_CA_CERTS",
    ]
    return {k: v for k in keys if (v := os.getenv(k))}


def _node_global_modules() -> str:
    """Return the npm global modules path (e.g. for requiring undici)."""
    try:
        return subprocess.check_output(
            ["npm", "root", "-g"], text=True,
            env={**os.environ}, timeout=10,
        ).strip()
    except Exception:
        return ""


class _UserSession:
    """A pair of MCP server sessions (Garmin + TP) for one user."""

    def __init__(self) -> None:
        self._exit_stack = AsyncExitStack()
        self._sessions: dict[str, ClientSession] = {}
        self._tool_registry: dict[str, tuple[str, dict[str, Any]]] = {}
        self.last_used: float = time.monotonic()

    async def start(self, creds: dict[str, str]) -> None:
        await self._exit_stack.__aenter__()
        connected_servers = 0
        failures: list[str] = []
        self.server_status: dict[str, str] = {}  # name → "ok" | error message

        servers = {
            "garmin": StdioServerParameters(
                command="uvx",
                args=[
                    "--python", "3.12",
                    "--from", "git+https://github.com/Taxuspt/garmin_mcp",
                    "garmin-mcp",
                ],
                env={
                    **_passthrough_env(),
                    "GARMIN_EMAIL": creds["garmin_email"],
                    "GARMIN_PASSWORD": creds["garmin_password"],
                },
            ),
        }

        if "tp_username" in creds:
            proxy_bootstrap = str(
                Path(__file__).with_name("proxy-bootstrap.js")
            )
            servers["trainingpeaks"] = StdioServerParameters(
                command="npx",
                args=["-y", "trainingpeaks-mcp@latest"],
                env={
                    **_passthrough_env(),
                    "TP_USERNAME": creds["tp_username"],
                    "TP_PASSWORD": creds["tp_password"],
                    # Node.js built-in fetch() bypasses http/https module patches.
                    # Our proxy-bootstrap.js uses undici's ProxyAgent instead.
                    "NODE_OPTIONS": f"-r {proxy_bootstrap}",
                    "NODE_GLOBAL_MODULES": _node_global_modules(),
                    "GLOBAL_AGENT_HTTP_PROXY": "http://localhost:3128",
                    "GLOBAL_AGENT_HTTPS_PROXY": "http://localhost:3128",
                },
            )

        for name, params in servers.items():
            t0 = time.monotonic()
            logger.info("[%s] Connecting …  (command: %s)", name, params.command)
            try:
                transport = await self._exit_stack.enter_async_context(
                    stdio_client(params)
                )
                read_stream, write_stream = transport
                session = await self._exit_stack.enter_async_context(
                    ClientSession(read_stream, write_stream)
                )
                await session.initialize()
                self._sessions[name] = session

                tools_result = await session.list_tools()
                for tool in tools_result.tools:
                    self._tool_registry[tool.name] = (name, {
                        "name": tool.name,
                        "description": tool.description or "",
                        "input_schema": tool.inputSchema,
                    })
                connected_servers += 1
                elapsed = time.monotonic() - t0
                self.server_status[name] = "ok"
                logger.info(
                    "[%s] ✓ Connected (%d tools, %.1fs)",
                    name, len(tools_result.tools), elapsed,
                )
            except Exception as exc:
                elapsed = time.monotonic() - t0
                err_msg = str(exc) or type(exc).__name__
                self.server_status[name] = err_msg
                logger.error(
                    "[%s] ✗ Failed to connect (%.1fs): %s",
                    name, elapsed, err_msg, exc_info=True,
                )
                failures.append(name)

        if connected_servers == 0 or not self._tool_registry:
            await self.stop()
            if failures:
                joined = ", ".join(failures)
                raise RuntimeError(
                    "No MCP tools are available for this user session. "
                    f"Failed to initialize: {joined}. "
                    "Check network access and account credentials, then reconnect."
                )
            raise RuntimeError("No MCP tools are available for this user session")

    async def stop(self) -> None:
        await self._exit_stack.aclose()

    def get_tools(self) -> list[dict[str, Any]]:
        return [
            {"name": s["name"], "description": s["description"], "input_schema": s["input_schema"]}
            for _, s in self._tool_registry.values()
        ]

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any], *, timeout: float = 30.0,
    ) -> str:
        self.last_used = time.monotonic()
        if tool_name not in self._tool_registry:
            return f"Error: unknown tool '{tool_name}'"

        server_name, _ = self._tool_registry[tool_name]
        session = self._sessions[server_name]
        result = await asyncio.wait_for(
            session.call_tool(tool_name, arguments), timeout=timeout,
        )

        parts = []
        for block in result.content:
            if hasattr(block, "text"):
                parts.append(block.text)
            else:
                parts.append(json.dumps(block.model_dump(), default=str))
        text = "\n".join(parts)

        if getattr(result, "isError", False):
            logger.warning(
                "[%s] Tool %s returned error: %s",
                server_name, tool_name, text[:500],
            )
            raise RuntimeError(f"Tool {tool_name} error: {text}")

        return text


class MCPPool:
    """Manages per-user MCP sessions with idle timeout eviction."""

    def __init__(self, store: CredentialStore) -> None:
        self._store = store
        self._sessions: dict[int, _UserSession] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._reaper_task: asyncio.Task | None = None

    async def start(self) -> None:
        self._reaper_task = asyncio.create_task(self._reaper())

    async def stop(self) -> None:
        if self._reaper_task:
            self._reaper_task.cancel()
        for uid in list(self._sessions):
            await self._evict(uid)

    # ── public API ───────────────────────────────────────────────────

    async def get_session(self, user_id: int) -> _UserSession | None:
        """Get or create a user's MCP session. Returns None if no creds stored."""
        lock = self._locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            if user_id in self._sessions:
                self._sessions[user_id].last_used = time.monotonic()
                return self._sessions[user_id]

            creds = await self._store.load(user_id)
            if creds is None:
                return None

            session = _UserSession()
            await session.start(creds)
            self._sessions[user_id] = session
            logger.info("Spawned MCP sessions for user %d", user_id)
            return session

    async def evict_user(self, user_id: int) -> None:
        """Tear down a specific user's sessions (e.g. on /disconnect)."""
        await self._evict(user_id)

    # ── internals ────────────────────────────────────────────────────

    async def _evict(self, user_id: int) -> None:
        session = self._sessions.pop(user_id, None)
        if session:
            try:
                await session.stop()
            except Exception:
                logger.exception("Error stopping session for user %d", user_id)
            logger.info("Evicted MCP sessions for user %d", user_id)

    async def _reaper(self) -> None:
        """Periodically evict idle sessions."""
        while True:
            await asyncio.sleep(60)
            now = time.monotonic()
            for uid in list(self._sessions):
                if now - self._sessions[uid].last_used > config.SESSION_IDLE_TIMEOUT:
                    logger.info("Idle timeout for user %d", uid)
                    await self._evict(uid)
