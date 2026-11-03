from __future__ import annotations

import hashlib

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logger import logger
from src.database import get_db
from src.models import Tenant

security = HTTPBearer()


def hash_api_key(raw_key: str) -> str:
    """Return the SHA-256 hex digest of *raw_key*."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


async def verify_tenant(
    credentials: HTTPAuthorizationCredentials = Security(security),
    db: AsyncSession = Depends(get_db),
) -> Tenant:
    """Validate the bearer token and return the corresponding active :class:`Tenant`.

    Raises :class:`fastapi.HTTPException` with HTTP 401 when the token is
    unknown or the tenant account is inactive.
    """
    key_hash: str = hash_api_key(credentials.credentials)

    result = await db.execute(select(Tenant).where(Tenant.api_key_hash == key_hash))
    tenant: Tenant | None = result.scalar_one_or_none()

    if tenant is None or not tenant.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or inactive API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    logger.debug("tenant_authenticated", tenant_id=str(tenant.id), tenant_name=tenant.name)

    return tenant
