from __future__ import annotations

from contextlib import asynccontextmanager
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from backend.app.scheduler import TradingScheduler
from backend.config.settings import get_settings


settings = get_settings()
scheduler = TradingScheduler(settings=settings)


@asynccontextmanager
async def lifespan(_: FastAPI):
    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown()


app = FastAPI(title="BurgerBot Trading Backend", lifespan=lifespan)


class BotpressExplainRequest(BaseModel):
    symbol: str


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "environment": settings.environment}


@app.get("/status")
def status() -> dict:
    return scheduler.snapshot_status()


@app.get("/watchlist")
def watchlist() -> dict[str, list[str]]:
    return {"symbols": settings.symbols}


@app.post("/botpress/decision/explain")
def botpress_explain(
    payload: BotpressExplainRequest,
    x_botpress_token: str | None = Header(default=None),
) -> dict:
    if settings.botpress_verify_token:
        if x_botpress_token != settings.botpress_verify_token:
            raise HTTPException(status_code=401, detail="Invalid Botpress token")
    try:
        return scheduler.build_symbol_explanation(payload.symbol)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
