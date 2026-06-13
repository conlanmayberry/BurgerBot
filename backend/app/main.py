from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend.app.scheduler import TradingScheduler
from backend.config.settings import get_settings


settings = get_settings()
scheduler = TradingScheduler(settings=settings)

DASHBOARD_PATH = Path(__file__).parent / "static" / "dashboard.html"


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


class RuntimeSettingsPatch(BaseModel):
    min_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    max_open_positions: int | None = Field(default=None, ge=1, le=100)
    max_position_pct: float | None = Field(default=None, gt=0.0, le=0.5)
    max_daily_drawdown_pct: float | None = Field(default=None, gt=0.0, le=0.5)
    stop_loss_pct: float | None = Field(default=None, gt=0.0, le=0.5)
    take_profit_pct: float | None = Field(default=None, gt=0.0, le=1.0)
    buy_threshold: float | None = Field(default=None, gt=0.0, le=1.0)
    sell_threshold: float | None = Field(default=None, ge=-1.0, lt=0.0)
    market_interval_seconds: int | None = Field(default=None, ge=15, le=3600)
    closed_market_interval_seconds: int | None = Field(default=None, ge=60, le=7200)
    allow_after_hours_trading: bool | None = None
    enable_protective_exits: bool | None = None
    enable_auto_execution: bool | None = None


def _verify_token(token: str | None) -> None:
    if settings.botpress_verify_token and token != settings.botpress_verify_token:
        raise HTTPException(status_code=401, detail="Invalid token")


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(DASHBOARD_PATH, media_type="text/html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "environment": settings.environment}


@app.get("/status")
def status() -> dict:
    return scheduler.snapshot_status()


@app.get("/connections")
def connections() -> dict:
    return scheduler.connection_report()


@app.get("/settings")
def get_runtime_settings() -> dict:
    return {
        "settings": scheduler.current_runtime_settings(),
        "note": "Changes via PATCH apply immediately but reset on container restart; edit .env to persist.",
    }


@app.patch("/settings")
def patch_runtime_settings(
    payload: RuntimeSettingsPatch,
    x_botpress_token: str | None = Header(default=None),
) -> dict:
    _verify_token(x_botpress_token)
    changes = payload.model_dump(exclude_none=True)
    if not changes:
        raise HTTPException(status_code=400, detail="No settings provided")
    if payload.stop_loss_pct is not None and payload.take_profit_pct is not None:
        if payload.stop_loss_pct >= payload.take_profit_pct:
            raise HTTPException(status_code=400, detail="stop_loss_pct must be below take_profit_pct")
    applied = scheduler.apply_runtime_settings(changes)
    return {"applied": changes, "settings": applied}


@app.get("/watchlist")
def watchlist() -> dict[str, list[str]]:
    return {"symbols": settings.symbols}


@app.post("/orders/cancel-all")
def cancel_all_orders(x_botpress_token: str | None = Header(default=None)) -> dict:
    _verify_token(x_botpress_token)
    try:
        canceled = scheduler.cancel_all_open_orders()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Cancel request failed: {exc}") from exc
    return {
        "canceled_count": len(canceled),
        "canceled_order_ids": [str(order.get("id", "")) for order in canceled],
    }


@app.post("/botpress/decision/explain")
def botpress_explain(
    payload: BotpressExplainRequest,
    x_botpress_token: str | None = Header(default=None),
) -> dict:
    _verify_token(x_botpress_token)
    try:
        return scheduler.build_symbol_explanation(payload.symbol)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
