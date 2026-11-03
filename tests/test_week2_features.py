from __future__ import annotations

import json
import pytest
import httpx
from httpx import ASGITransport
import redis.asyncio as aioredis
from sqlalchemy import select,text

from src.config import settings
from src.core.auth import hash_api_key
from src.database import AsyncSessionLocal, Base, engine
from src.gateway.circuit_breaker import CircuitBreaker, CircuitState
from src.main import app
from src.models import Tenant

pytestmark = pytest.mark.asyncio

VALID_KEY = "ag-live-week2-testkey"


@pytest.fixture(scope="function", autouse=True)
async def setup_test_environment():
    """Ensure Postgres tables, Redis connection, and test tenant exist."""
    # 1. Database tables
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("TRUNCATE TABLE semantic_cache CASCADE;"))

    # 2. Redis connection attached to app.state
    redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    await redis_client.flushdb()  # Clean slate for each test
    app.state.redis = redis_client

    # 3. Seed Tenant with a low TPM limit (500) for easy rate-limit testing
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Tenant).where(Tenant.api_key_hash == hash_api_key(VALID_KEY))
        )
        existing = result.scalar_one_or_none()
        if existing is None:
            tenant = Tenant(
                name="Week 2 Test Tenant",
                api_key_hash=hash_api_key(VALID_KEY),
                tpm_limit=500,  # Low limit for testing
                monthly_budget_usd=50.0,
            )
            session.add(tenant)
            await session.commit()

    yield

    # Teardown
    await redis_client.flushdb()
    await redis_client.aclose()


@pytest.fixture(scope="function")
async def client():
    """Sets up an in-memory ASGI client for both external caller and internal proxy."""
    transport = ASGITransport(app=app)
    app.state.http_client = httpx.AsyncClient(
        transport=transport,
        base_url="http://localhost:8000",
        timeout=httpx.Timeout(2.0, connect=2.0),
    )

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://localhost:8000",
    ) as ac:
        yield ac

    await app.state.http_client.aclose()


# ============================================================================
# 1. Semantic Cache Tests
# ============================================================================

async def test_semantic_cache_hit_on_similar_prompt(client: httpx.AsyncClient) -> None:
    """A prompt miss should cache the response; a semantically similar prompt should HIT."""
    # Request 1: Fresh prompt (MISS)
    res1 = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {VALID_KEY}"},
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "Explain quantum computing briefly"}],
            "max_tokens": 50,
        },
    )
    assert res1.status_code == 200
    assert res1.headers.get("X-Cache-Status") == "MISS"
    assert res1.headers.get("X-Routed-Provider") == "primary"

    # Request 2: Semantically similar prompt ("Give a brief explanation of quantum computing")
    res2 = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {VALID_KEY}"},
        json={
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "user", "content": "Give a brief explanation of quantum computing"}
            ],
            "max_tokens": 50,
        },
    )
    assert res2.status_code == 200
    assert res2.headers.get("X-Cache-Status") == "HIT"
    assert res2.headers.get("X-Routed-Provider") == "semantic_cache"
    assert "[DONE]" in res2.text


async def test_bypass_cache_header_forces_miss(client: httpx.AsyncClient) -> None:
    """Sending X-Bypass-Cache: true must skip the cache even if a match exists."""
    prompt = "What is the capital of France?"

    # Populate cache
    await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {VALID_KEY}"},
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 20,
        },
    )

    # Send exact same prompt with bypass header
    res_bypassed = await client.post(
        "/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {VALID_KEY}",
            "X-Bypass-Cache": "true",
        },
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 20,
        },
    )
    assert res_bypassed.status_code == 200
    assert res_bypassed.headers.get("X-Cache-Status") == "MISS"
    assert res_bypassed.headers.get("X-Routed-Provider") == "primary"


# ============================================================================
# 2. Rate Limiting Tests (Redis Lua Script)
# ============================================================================

async def test_rate_limiter_blocks_excessive_tokens(client: httpx.AsyncClient) -> None:
    """Requests exceeding tenant TPM limit (500 tokens) must return HTTP 429."""
    # Test tenant has tpm_limit = 500. Requesting 600 tokens should immediately fail.
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {VALID_KEY}"},
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "Test rate limiter"}],
            "max_tokens": 600,  # Exceeds 500 limit
        },
    )
    assert response.status_code == 429
    assert "Retry-After" in response.headers
    assert response.headers.get("X-RateLimit-Limit") == "500"
    data = response.json()
    assert "Rate limit exceeded" in data["detail"]


# ============================================================================
# 3. Circuit Breaker Tests
# ============================================================================

async def test_circuit_breaker_trips_to_open_after_failures(client: httpx.AsyncClient) -> None:
    """5 consecutive failures must trip the primary circuit to OPEN."""
    redis = app.state.redis
    cb = CircuitBreaker(redis=redis, failure_threshold=5, recovery_timeout=30)

    # Initial state must be CLOSED
    assert await cb.get_state("primary") == CircuitState.CLOSED

    # Simulate 5 failed calls through chaos mode
    for _ in range(5):
        await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {VALID_KEY}"},
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": f"Chaos failure {_}"}],
                "simulate_failure": "503",
                "max_tokens": 10,
            },
        )

    # Primary breaker must now be OPEN in Redis
    state = await cb.get_state("primary")
    assert state == CircuitState.OPEN
    assert await cb.can_execute("primary") is False