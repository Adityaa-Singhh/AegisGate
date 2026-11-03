from __future__ import annotations

import json
import time
import uuid
from typing import AsyncGenerator

import httpx
import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status, Header
from fastapi.responses import StreamingResponse

from src.core.auth import verify_tenant
from src.core.logger import logger
from src.database import AsyncSessionLocal
from src.gateway.circuit_breaker import CircuitBreaker
from src.gateway.rate_limiter import enforce_tpm_rate_limit
from src.gateway.schemas import ChatCompletionRequest
from src.gateway.semantic_cache import check_semantic_cache, save_to_semantic_cache
from src.models import Tenant, UsageLog

router = APIRouter(tags=["gateway"])

PROVIDER_CHAIN: list[dict] = [
    {
        "name": "primary",
        "url": "http://localhost:8000/mock-llm/primary/chat/completions",
    },
    {
        "name": "fallback_groq",
        "url": "http://localhost:8000/mock-llm/fallback_groq/chat/completions",
    },
]


async def log_usage_background(
    tenant_id: str,
    request_id: str,
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: float,
    status_code: int,
    is_fallback: bool,
) -> None:
    """Write a UsageLog row using an isolated session (Rule 3: no session held across I/O)."""
    session = AsyncSessionLocal()
    try:
        log = UsageLog(
            tenant_id=uuid.UUID(tenant_id),
            request_id=request_id,
            provider=provider,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
            status_code=status_code,
            is_fallback=is_fallback,
        )
        session.add(log)
        await session.commit()
    except Exception:
        bound_logger: structlog.BoundLogger = structlog.get_logger()
        bound_logger.error(
            "usage_log_failed",
            request_id=request_id,
            tenant_id=tenant_id,
            provider=provider,
        )
    finally:
        await session.close()

async def save_cache_background(prompt: str, response_text: str, model: str) -> None:
    """Save completed prompt/response pair to pgvector using an isolated session."""
    session = AsyncSessionLocal()
    try:
        await save_to_semantic_cache(
            db=session,
            prompt=prompt,
            response_text=response_text,
            model=model
        )
    except Exception:
        logger.warning("semantic_cache_save_failed",model=model)
    finally:
        await session.close()

async def stream_cached_response(cached_text:str) -> AsyncGenerator[bytes, None]:
    """Emit an SSE stream from a cached string."""
    chunk = {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "semantic-cache",
        "choices": [
            {
                "index": 0,
                "delta": {"content" : cached_text},
                "finish_reason": "stop"
            }
        ]
    }
    yield f"data: {json.dumps(chunk)} \n\n".encode("utf-8")
    yield b"data: [DONE]\n\n"

@router.post("/v1/chat/completions")
async def chat_completions(
    payload: ChatCompletionRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    tenant: Tenant = Depends(verify_tenant),
    x_bypass_cache: str | None = Header(default="false")
) -> StreamingResponse:
    start_time = time.perf_counter()
    request_id: str = getattr(request.state, "request_id", str(uuid.uuid4()))
    client: httpx.AsyncClient = request.app.state.http_client
    redis_client = getattr(request.app.state, "redis", None)

    prompt_text = " ".join(m.content for m in payload.messages)
    prompt_tokens = len(prompt_text.split())
    estimated_total_tokens = prompt_tokens + getattr(payload, "max_tokens", 50)

    #1. Rate Limiting (Atomic Redis Lua Script)

    if redis_client:
        await enforce_tpm_rate_limit(
            redis = redis_client,
            tenant_id= tenant.id,
            requested_tokens=estimated_total_tokens,
            tpm_limit=tenant.tpm_limit,
        )

    # 2. Semantic Cache Lookup (pgvector)

    bypass_cache = (x_bypass_cache or "").lower() == "true" or payload.simulate_failure != "none"
    if not bypass_cache and prompt_text:
        async with AsyncSessionLocal() as session:
            cached_result = await check_semantic_cache(
                db = session,
                prompt = prompt_text,
                model = payload.model,
                similarity_threshold=0.92
        )
            if cached_result:
                latency_ms = (time.perf_counter() - start_time) * 1000
                logger.info("semantic_cache_hit", request_id = request_id, model = payload.model)

                background_tasks.add_task(
                    log_usage_background,
                    tenant_id=str(tenant.id),
                    request_id=request_id,
                    provider="semantic_cache",
                    model=payload.model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=len(cached_result.split()),
                    latency_ms=latency_ms,
                    status_code=200,
                    is_fallback=False,
                )

                return StreamingResponse(
                    stream_cached_response(cached_result),
                    media_type="text/event-stream",
                    headers={
                        "X-Request-ID": request_id,
                        "X-Routed-Provider": "semantic_cache",
                        "X-Is-Fallback": "false",
                        "X-Cache-Status": "HIT",
                        "Cache-Control": "no-cache",
                        "X-Accel-Buffering": "no"
                    }
                )
            
    
    #3. Upstream Routing with Circuit Breaker Failover
    # Build the upstream request body — include simulate_failure so the
    # mock provider can apply the chaos mode, real providers ignore unknown fields.
    body: dict = payload.model_dump()

    response: httpx.Response | None = None
    chosen_provider: str = ""
    provider_index: int = 0

    circuit_breaker = CircuitBreaker(redis=redis_client) if redis_client else None

    for idx, provider in enumerate(PROVIDER_CHAIN):
        provider_name = provider["name"]

        #check circuit breaker before opening connection 
        if circuit_breaker and not await circuit_breaker.can_execute(provider_name):
            logger.warning(
                "circuit_breaker_tripped_skipping",
                provider=provider_name,
                request_id = request_id,
            )
            continue

        req = client.build_request(
            "POST",
            provider["url"],
            json=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            response = await client.send(req, stream=True)
        except (httpx.TimeoutException, httpx.RequestError):
            if circuit_breaker:
                await circuit_breaker.record_failure(provider_name)
            logger.warning(
                "provider_request_failed",
                provider=provider["name"],
                request_id=request_id,
            )
            continue

        if response.status_code != 200:
            await response.aclose()
            if circuit_breaker:
                await circuit_breaker.record_failure(provider_name)
            logger.warning(
                "provider_non_200",
                provider=provider["name"],
                status_code=response.status_code,
                request_id=request_id,
            )
            response = None
            continue

        # If 200 OK, record success in circuit breaker
        if circuit_breaker:
            await circuit_breaker.record_success(provider_name)

        provider_index = idx
        chosen_provider = provider["name"]
        break

    if response is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, 
            detail="All providers failed or circuits are open"
            )

    logger.info(
        "request_routed",
        provider=chosen_provider,
        is_fallback=(provider_index > 0),
        request_id=request_id,
    )

    _response = response

    async def stream_and_cleanup() -> AsyncGenerator[bytes, None]:
        accumulated_chunks: list[str] = []
        try:
            async for chunk in _response.aiter_bytes():
                yield chunk
                #Extract text for semantic caching

                try:
                    text_chunk = chunk.decode("utf-8")
                    for line in text_chunk.splitlines():
                        if line.startswith("data: ") and line != "data: [DONE]":
                            data_payload = json.loads(line.removeprefix("data: ").strip())
                            delta = data_payload.get("choices", [{}])[0].get("delta",{})
                            content = delta.get("content")
                            if content:
                                accumulated_chunks.append(content)
                except Exception:
                    pass   # Why did here the exception is not raised
        finally:
            await _response.aclose()
            latency_ms = (time.perf_counter() - start_time) * 1000

            # log usage in background

            background_tasks.add_task(
                log_usage_background,
                tenant_id=str(tenant.id),
                request_id=request_id,
                provider=chosen_provider,
                model=payload.model,
                prompt_tokens=len(
                    " ".join(m.content for m in payload.messages).split()
                ),
                completion_tokens=50,
                latency_ms=latency_ms,
                status_code=200,
                is_fallback=(provider_index > 0),
            )

            # Save response to semantic cache for future requests
            full_response_text = "".join(accumulated_chunks).strip()
            if full_response_text and prompt_text and payload.simulate_failure == "none":
                background_tasks.add_task(
                    save_cache_background,
                    prompt=prompt_text,
                    response_text=full_response_text,
                    model=payload.model
                )

    return StreamingResponse(
        stream_and_cleanup(),
        media_type="text/event-stream",
        headers={
            "X-Request-ID": request_id,
            "X-Routed-Provider": chosen_provider,
            "X-Is-Fallback": str(provider_index > 0).lower(),
            "X-Cache-Status": "MISS",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
