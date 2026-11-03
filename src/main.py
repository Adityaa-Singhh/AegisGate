from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
import redis.asyncio as aioredis
import structlog
from fastapi import FastAPI
from sqlalchemy import text

from src.config import settings
from src.core.logger import CorrelationIdMiddleware, logger
from src.database import AsyncSessionLocal, Base, engine
from src.gateway.mock_provider import router as mock_router
from src.gateway.router import router as gateway_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # STARTUP
    # 1. Create tables (temporary, before Alembic)
    async with engine.begin() as conn:
        await conn.execute(text("Create Extension if not exists vector;"))
        await conn.run_sync(Base.metadata.create_all)

    # 2. Verify Postgres connectivity
    async with AsyncSessionLocal() as session:
        await session.execute(text("SELECT 1"))
    logger.info("database_connected", url=settings.DATABASE_URL.split("@")[-1])

    # 3. Verify Redis connectivity
    redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    await redis_client.ping()
    app.state.redis = redis_client
    logger.info("redis_connected", url=settings.REDIS_URL)

    # 4. Initialize shared httpx client
    timeout = httpx.Timeout(settings.HTTP_CLIENT_TIMEOUT, connect=settings.HTTP_CLIENT_TIMEOUT)
    limits = httpx.Limits(
        max_keepalive_connections=settings.HTTP_MAX_KEEPALIVE,
        max_connections=settings.HTTP_MAX_CONNECTIONS,
    )
    app.state.http_client = httpx.AsyncClient(timeout=timeout, limits=limits)
    logger.info("http_client_initialized", timeout=settings.HTTP_CLIENT_TIMEOUT)

    yield

    # SHUTDOWN
    await app.state.http_client.aclose()
    await app.state.redis.aclose()
    await engine.dispose()
    logger.info("shutdown_complete")


app = FastAPI(title="AegisGate", version="1.0.0", lifespan=lifespan)

app.add_middleware(CorrelationIdMiddleware)

app.include_router(mock_router)
app.include_router(gateway_router)


@app.get("/health", tags=["ops"])
async def health_check() -> dict:
    # Check DB
    async with AsyncSessionLocal() as session:
        await session.execute(text("SELECT 1"))
    # Check Redis
    await app.state.redis.ping()
    return {"status": "ok", "environment": settings.ENVIRONMENT}
