from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = Field(default="gpt-4o-mini", description="Model identifier")
    messages: list[ChatMessage]
    stream: bool = True
    max_tokens: int = Field(default=50, ge=1, description="Maximum completion tokens")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    simulate_failure: Literal["none", "503", "429", "timeout"] = "none"
