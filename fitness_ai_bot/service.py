"""Reusable agent service wrapper around credentials, MCP sessions, and AI answering."""

from fitness_ai_bot.agent import ask
from fitness_ai_bot.credential_store import CredentialStore
from fitness_ai_bot.mcp_client import MCPPool


class FitnessAgentService:
    """High-level interface for connecting users and answering questions."""

    def __init__(self) -> None:
        self._store = CredentialStore()
        self._pool = MCPPool(self._store)

    async def start(self) -> None:
        await self._store.open()
        await self._pool.start()

    async def stop(self) -> None:
        await self._pool.stop()
        await self._store.close()

    async def has_credentials(self, user_id: int) -> bool:
        return await self._store.has_credentials(user_id)

    async def connect_user(self, user_id: int, creds: dict[str, str]) -> None:
        await self._store.save(user_id, creds)
        await self._pool.evict_user(user_id)

    async def disconnect_user(self, user_id: int) -> bool:
        await self._pool.evict_user(user_id)
        return await self._store.delete(user_id)

    async def ask_user(self, user_id: int, question: str) -> str:
        session = await self._pool.get_session(user_id)
        if session is None:
            raise RuntimeError("User has no connected credentials")
        return await ask(question, session)
