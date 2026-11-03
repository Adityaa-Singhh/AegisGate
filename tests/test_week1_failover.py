from __future__ import annotations

import pytest
import httpx
from httpx import ASGITransport
from sqlalchemy import select, text

from src.core.auth import hash_api_key
from src.database import AsyncSessionLocal, Base, engine
from src.main import app
from src.models import Tenant

pytestmark = pytest.mark.asyncio

VALID_KEY = "ag-live-testkey123456"
INVALID_KEY = "invalid-key-000"


@pytest.fixture(scope="function", autouse=True)
async def setup_db():
    """Function-scoped fixture: ensures tables and seed tenant exist in the active test loop."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("TRUNCATE TABLE semantic_cache CASCADE;"))

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Tenant).where(Tenant.api_key_hash == hash_api_key(VALID_KEY))
        )
        existing = result.scalar_one_or_none()
        if existing is None:
            tenant = Tenant(
                name="Test Tenant",
                api_key_hash=hash_api_key(VALID_KEY),
                tpm_limit=100000,
                monthly_budget_usd=100.0,
            )
            session.add(tenant)
            await session.commit()

    yield


@pytest.fixture(scope="function")
async def client():
    """Initializes app.state.http_client with ASGITransport for in-memory routing and yields client."""
    transport = ASGITransport(app=app)
    # Configure in-memory transport for the gateway's internal HTTP client so mock LLM calls resolve
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


async def test_unauthorized_rejects_invalid_key(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {INVALID_KEY}"},
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "Hi"}],
        },
    )
    assert response.status_code == 401


async def test_primary_streaming_success(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {VALID_KEY}"},
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "Hi"}],
            "simulate_failure": "none",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("X-Is-Fallback") == "false"
    assert response.headers.get("X-Routed-Provider") == "primary"
    assert "[DONE]" in response.text


async def test_failover_to_secondary_on_503(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {VALID_KEY}"},
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "Hello"}],
            "simulate_failure": "503",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("X-Is-Fallback") == "true"
    assert response.headers.get("X-Routed-Provider") == "fallback_groq"
    assert "fallback_groq" in response.text
