from __future__ import annotations

import asyncio
import json
import time
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from src.core.logger import logger

router = APIRouter(prefix="/mock-llm", tags=["mock-llm"])


@router.post("/{provider_name}/chat/completions")
async def mock_chat_completions(
    provider_name: str, request: Request
) -> StreamingResponse:
    payload: dict = await request.json()
    simulate_failure: str = payload.get("simulate_failure", "none")

    logger.info(
        "mock_provider_request",
        provider=provider_name,
        simulate_failure=simulate_failure,
    )

    if provider_name == "primary":
        if simulate_failure == "503":
            raise HTTPException(
                status_code=503, detail="Simulated Primary Provider Outage"
            )
        if simulate_failure == "429":
            raise HTTPException(
                status_code=429, detail="Simulated Rate Limit Exceeded"
            )
        if simulate_failure == "timeout":
            await asyncio.sleep(10)
            raise HTTPException(
                status_code=504, detail="Simulated Gateway Timeout"
            )

    async def generator() -> AsyncGenerator[bytes, None]:
        for i in range(6):
            chunk: dict = {
                "id": f"chatcmpl-{provider_name}-{i}",
                "object": "chat.completion.chunk",
                "choices": [
                    {
                        "delta": {"content": f"[{provider_name}] token {i + 1} "},
                        "index": 0,
                        "finish_reason": None,
                    }
                ],
            }
            yield f"data: {json.dumps(chunk)}\n\n".encode()
            await asyncio.sleep(0.04)
        yield b"data: [DONE]\n\n"

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
