from __future__ import annotations

from enum import Enum
from redis.asyncio import Redis

class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"

class CircuitBreaker:
    """Distributed Redis-backed Circuit Breaker for upstream LLM providers."""
    def __init__(
            self,
            redis: Redis,
            failure_threshold: int = 5,
            recovery_timeout: int = 30,
            failure_window: int = 60,
    ) -> None:
        self.redis = redis
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_window = failure_window
    def _state_key(self, provider: str) -> str:
        return f"cb:{provider}:state"
    
    def _fail_count_key(self, provider: str) -> str:
        return f"cb:{provider}:failures"
    
    def _half_open_probe_key(self, provider:str) -> str:
        return f"cb:{provider}:half_open_probe"

    async def get_state(self, provider: str) -> CircuitState:
        """Determines the current circuit state based on Redis keys."""
        raw_state = await self.redis.get(self._state_key(provider))
        if raw_state is not None:
            # Key exists in Redis with TTL -> Circuit is explicitly OPEN
            return CircuitState.OPEN

        # If it was OPEN previously and expired, check if a probe is in-flight
        probe_active = await self.redis.get(self._half_open_probe_key(provider))
        if probe_active is not None:
            return CircuitState.HALF_OPEN
        return CircuitState.CLOSED

    async def can_execute(self, provider:str) -> bool:
        """Determines if a request is allowed to reach the upstream provider."""
        state = await self.get_state(provider)

        if state == CircuitState.CLOSED:
            return True

        if state == CircuitState.OPEN:
            # Cooldown is still active -> fail fast
            return False

        # If HALF_OPEN: Allow only one probe request through using atomic SET NX
        probe_acquired = await self.redis.set(
            self._half_open_probe_key(provider),
            "in_flight",
            ex = self.recovery_timeout,
            nx = True
        )
        return bool(probe_acquired)

    async def record_success(self, provider: str) -> None:
        """Resets the circuit breaker upon a successful upstream request."""
        # Clean up failures, open state, and half-open probe lock
        pipe = self.redis.pipeline()
        pipe.delete(self._state_key(provider))
        pipe.delete(self._fail_count_key(provider))
        pipe.delete(self._half_open_probe_key(provider))
        await pipe.execute()

    async def record_failure(self, provider: str) -> None:
        """Records an upstream failure and trips the breaker if threshold is exceeded"""
        fail_key = self._fail_count_key(provider)
        state_key = self._state_key(provider)

        current_state = await self.get_state(provider)

        if current_state == CircuitState.HALF_OPEN:
            # Canary request failed during trial -> trip back to OPEN immediately
            pipe = self.redis.pipeline()
            pipe.set(state_key, CircuitState.OPEN.value, ex=self.recovery_timeout)
            pipe.delete(self._half_open_probe_key(provider))
            await pipe.execute()
            return 

        # Atomically increment failure counter within sliding window
        current_fails = await self.redis.incr(fail_key)
        if current_fails == 1:
            await self.redis.expire(fail_key, self.failure_window)

        if current_fails >= self.failure_threshold:
            # Trip the circuit to OPEN with recovery cooldown
            pipe = self.redis.pipeline()
            pipe.set(state_key, CircuitState.OPEN.value, ex = self.recovery_timeout)
            pipe.delete(fail_key)
            await pipe.execute()
    