from __future__ import annotations

import uuid 
from dataclasses import dataclass
from fastapi import HTTPException, status
from redis.asyncio import Redis

# Atomic Lua script executed directly in Redis memory.
# Keys[1]: Rate limit key (e.g, 'rate_limit:tpm:{tenant_id}')
# Argv[1]: Requested tokens to consume
# Argv[2]: Maximum Tokens-Per-Minute(TPM) limit
# Argv[3]: Window duration in seconds(60s)
#
# Returns: {allowed (1 or 0), current_usage, limit, ttl_remaining}

LUA_RATE_LIMITER_SCRIPT = """
local key = KEYS[1]
local requested = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local window = tonumber(ARGV[3])

local current = tonumber(redis.call('GET', key) or "0")

if current + requested > limit then
    local ttl = redis.call('TTL', key)
    if ttl < 0 then
        ttl = window
    end
    return {0, current, limit, ttl}
else
    local new_val = redis.call('INCRBY', key, requested)
    if current == 0 then
        redis.call('EXPIRE', key, window)
    end
    local ttl = redis.call('TTL', key)
    return {1, new_val, limit, ttl}
end
"""

@dataclass(frozen=True)
class RateLimitResult:
    allowed:bool
    current_tokens: int
    limit: int
    retry_after: int

class TokenRateLimiter:
    """Manages distributed atomic Tokens-Per-Minute (TPM) rate limiting."""
    def __init__(self, redis: Redis, window_seconds: int = 60) -> None:
        self.redis = redis
        self.window_seconds = window_seconds
        # Register script with Redis for SHA- based evaluation caching
        self._script = self.redis.register_script(LUA_RATE_LIMITER_SCRIPT)

    def _build_key(self, tenant_id: uuid.UUID | str) -> str:
        return f"rate_limit:tpm:{tenant_id}"

    async def check_and_consume(
            self,
            tenant_id: uuid.UUID | str,
            requested_tokens: int,
            tpm_limit: int,
    ) -> RateLimitResult:
        """Atomically checks and consumes tokens for a tenant within the active window."""
        key = self._build_key(tenant_id)

        # Execute registered script atomically
        res = await self._script(
            keys=[key],
            args=[requested_tokens, tpm_limit, self.window_seconds],
        )

        allowed_int, current, limit, ttl = res
        return RateLimitResult(
            allowed=bool(allowed_int),
            current_tokens=int(current),
            limit=int(limit),
            retry_after=max(0, int(ttl))
        )
async def enforce_tpm_rate_limit(
        redis: Redis,
        tenant_id: uuid.UUID | str,
        requested_tokens: int,
        tpm_limit: int,
) -> None:
    """FastAPI helper that raises HTTP 429 if the tenant exceeds their TPM limit."""
    limiter = TokenRateLimiter(redis=redis)
    result = await limiter.check_and_consume(
        tenant_id=tenant_id,
        requested_tokens=requested_tokens,
        tpm_limit=tpm_limit
    )
    if not result.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Rate limit exceeded: Tenant requested {requested_tokens},"
            ),
            headers={
                "Retry-After": str(result.retry_after),
                "X-RateLimit-Limit": str(result.limit),
                "X-RateLimit-Remaining": str(max(0, result.limit - result.current_tokens)),
                "X-RateLimit-Reset": str(result.retry_after),
            }
        )