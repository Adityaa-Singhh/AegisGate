from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select

from src.config import settings
from src.core.auth import hash_api_key
from src.database import AsyncSessionLocal, Base, engine
from src.models import Tenant

RAW_API_KEY = "ag-live-testkey123456"
TENANT_NAME = "Acme Corp"


async def seed_db() -> None:
    # 1. Create all tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 2. Open session and check for existing tenant
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Tenant).where(Tenant.api_key_hash == hash_api_key(RAW_API_KEY))
        )
        existing = result.scalar_one_or_none()

        if existing is not None:
            print(f"[INFO] Tenant '{TENANT_NAME}' already exists -- skipping seed (idempotent).")
            return

        # 3. Create and persist the tenant
        tenant = Tenant(
            name=TENANT_NAME,
            api_key_hash=hash_api_key(RAW_API_KEY),
            tpm_limit=100000,
            monthly_budget_usd=100.0,
        )
        session.add(tenant)
        await session.commit()

    # 4. Print success + curl commands
    print("\n[OK] Seeding complete!")
    print(f"\nTenant: {TENANT_NAME}")
    print(f"API Key: {RAW_API_KEY}")
    print("\n--- Test curl commands ---")
    print(f"""
    # 1. Health check
    curl http://localhost:8000/health

    # 2. Successful streaming request
    curl -N -X POST http://localhost:8000/v1/chat/completions \\
      -H "Authorization: Bearer {RAW_API_KEY}" \\
      -H "Content-Type: application/json" \\
      -d '{{"model": "gpt-4o-mini", "messages": [{{"role": "user", "content": "Hello"}}], "simulate_failure": "none"}}'

    # 3. Failover test (primary 503 → fallback)
    curl -N -X POST http://localhost:8000/v1/chat/completions \\
      -H "Authorization: Bearer {RAW_API_KEY}" \\
      -H "Content-Type: application/json" \\
      -d '{{"model": "gpt-4o-mini", "messages": [{{"role": "user", "content": "Hello"}}], "simulate_failure": "503"}}'
    """)


if __name__ == "__main__":
    asyncio.run(seed_db())
