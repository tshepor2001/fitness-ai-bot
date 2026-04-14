"""Optional HTTP API adapter for FitnessAgentService."""

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException
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


app = FastAPI(title="Fitness AI Bot API", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/users/{user_id}/connect")
async def connect(user_id: int, body: ConnectRequest) -> dict[str, str]:
    if (body.tp_username is None) ^ (body.tp_password is None):
        raise HTTPException(status_code=400, detail="Provide both tp_username and tp_password, or neither.")

    creds = {
        "garmin_email": body.garmin_email,
        "garmin_password": body.garmin_password,
    }
    if body.tp_username and body.tp_password:
        creds["tp_username"] = body.tp_username
        creds["tp_password"] = body.tp_password

    await service.connect_user(user_id, creds)
    return {"status": "connected"}


@app.delete("/users/{user_id}/connect")
async def disconnect(user_id: int) -> dict[str, bool]:
    deleted = await service.disconnect_user(user_id)
    return {"deleted": deleted}


@app.post("/users/{user_id}/ask")
async def ask(user_id: int, body: AskRequest) -> dict[str, str]:
    if not await service.has_credentials(user_id):
        raise HTTPException(status_code=404, detail="User credentials not found. Connect first.")

    try:
        answer = await service.ask_user(user_id, body.question)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Agent error: {exc}") from exc

    return {"answer": answer}


def main() -> None:
    uvicorn.run("fitness_ai_bot.http_api:app", host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()