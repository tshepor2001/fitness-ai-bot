"""Optional HTTP API adapter for FitnessAgentService."""

from contextlib import asynccontextmanager
import hashlib
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from fitness_ai_bot.service import FitnessAgentService

service = FitnessAgentService()


class ConnectRequest(BaseModel):
    garmin_email: str
    garmin_password: str
    tp_username: str | None = None
    tp_password: str | None = None


class AskRequest(BaseModel):
    question: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    await service.start()
    try:
        yield
    finally:
        await service.stop()


app = FastAPI(title="Sentinel Coach API", version="0.2.0", lifespan=lifespan)
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _internal_user_id(user_id: str) -> int:
    """Map any non-empty user identifier to a stable integer key."""
    normalized = user_id.strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="user_id must not be empty")
    digest = hashlib.sha256(normalized.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=False)


def _agent_error_to_http(exc: Exception) -> HTTPException:
    msg = str(exc)
    lower = msg.lower()

    if "credit balance is too low" in lower or "plans & billing" in lower:
        return HTTPException(
            status_code=402,
            detail="Anthropic account has insufficient credits. Add credits in Plans & Billing and retry.",
        )

    if "invalid x-api-key" in lower or "api key" in lower and "invalid" in lower:
        return HTTPException(
            status_code=401,
            detail="Anthropic API key is invalid. Update ANTHROPIC_API_KEY and retry.",
        )

    return HTTPException(status_code=500, detail=f"Agent error: {msg}")


@app.get("/")
async def frontend() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/users")
async def list_users() -> list[dict]:
    return await service.list_users()


@app.post("/users/{user_id}/connect")
async def connect(user_id: str, body: ConnectRequest) -> dict[str, Any]:
    internal_uid = _internal_user_id(user_id)
    if (body.tp_username is None) ^ (body.tp_password is None):
        raise HTTPException(status_code=400, detail="Provide both tp_username and tp_password, or neither.")

    creds = {
        "garmin_email": body.garmin_email,
        "garmin_password": body.garmin_password,
    }
    if body.tp_username and body.tp_password:
        creds["tp_username"] = body.tp_username
        creds["tp_password"] = body.tp_password

    await service.connect_user(internal_uid, creds, label=user_id.strip())
    try:
        await service.validate_user_session(internal_uid)
    except Exception as exc:
        await service.disconnect_user(internal_uid)
        raise HTTPException(
            status_code=502,
            detail=(
                "Connected credentials could not start MCP tools. "
                f"Reason: {exc}"
            ),
        ) from exc

    # Pre-fetch fitness data so first ask is fast
    try:
        await service.sync_cache(internal_uid)
    except Exception:
        pass  # cache miss is fine — will retry on first ask

    server_status = await service.get_server_status(internal_uid)
    return {
        "status": "connected",
        "sources": await service.get_sources(internal_uid),
        "servers": server_status,
    }


@app.post("/users/{user_id}/reconnect")
async def reconnect(user_id: str) -> dict[str, Any]:
    """Reconnect an existing user using their stored credentials."""
    internal_uid = _internal_user_id(user_id)
    if not await service.has_credentials(internal_uid):
        raise HTTPException(status_code=404, detail="No stored credentials for this user.")

    # Keep label in sync with the name used to reconnect
    await service.update_user_label(internal_uid, user_id.strip())

    try:
        await service.validate_user_session(internal_uid)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not start MCP tools: {exc}",
        ) from exc

    try:
        await service.sync_cache(internal_uid)
    except Exception:
        pass

    server_status = await service.get_server_status(internal_uid)
    return {
        "status": "connected",
        "sources": await service.get_sources(internal_uid),
        "servers": server_status,
    }


@app.delete("/users/{user_id}/connect")
async def disconnect(user_id: str) -> dict[str, bool]:
    internal_uid = _internal_user_id(user_id)
    deleted = await service.disconnect_user(internal_uid)
    return {"deleted": deleted}


@app.post("/users/{user_id}/ask")
async def ask(user_id: str, body: AskRequest) -> dict[str, Any]:
    internal_uid = _internal_user_id(user_id)
    if not await service.has_credentials(internal_uid):
        raise HTTPException(status_code=404, detail="User credentials not found. Connect first.")

    try:
        answer, sources = await service.ask_user(internal_uid, body.question)
    except Exception as exc:
        raise _agent_error_to_http(exc) from exc

    return {"answer": answer, "sources": sources}


@app.get("/users/{user_id}/history")
async def get_history(
    user_id: str, limit: int = 50, offset: int = 0,
) -> list[dict]:
    internal_uid = _internal_user_id(user_id)
    return await service.get_history(internal_uid, limit=limit, offset=offset)


@app.delete("/users/{user_id}/history")
async def delete_history(user_id: str) -> dict[str, int]:
    internal_uid = _internal_user_id(user_id)
    deleted = await service.delete_history(internal_uid)
    return {"deleted": deleted}


def main() -> None:
    uvicorn.run("fitness_ai_bot.http_api:app", host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()