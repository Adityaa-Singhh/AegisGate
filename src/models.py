from __future__ import annotations

import uuid
from datetime import datetime
from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from src.database import Base


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    api_key_hash: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    tpm_limit: Mapped[int] = mapped_column(Integer, default=100000, nullable=False)
    monthly_budget_usd: Mapped[float] = mapped_column(
        Float, default=100.0, nullable=False
    )
    current_spend_usd: Mapped[float] = mapped_column(
        Float, default=0.0, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    usage_logs: Mapped[list[UsageLog]] = relationship(
        "UsageLog", back_populates="tenant", lazy="raise"
    )


class UsageLog(Base):
    __tablename__ = "usage_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, nullable=False
    )
    request_id: Mapped[str] = mapped_column(
        String(64), index=True, nullable=False
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, default=200, nullable=False)
    is_fallback: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    tenant: Mapped[Tenant] = relationship(
        "Tenant", back_populates="usage_logs", lazy="raise"
    )
class SemanticCache(Base):
    __tablename__ = "semantic_cache"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    prompt: Mapped[str] = mapped_column(Text,nullable=False)
    response_text:Mapped[str] = mapped_column(Text, nullable=False)
    model:Mapped[str] = mapped_column(String(128),nullable=False, index=True)
    # 384 is the dimension matching FastEmbed 'BAAI/bge-small-en-v1.5'
    embedding = mapped_column(Vector(384),nullable=False) 
    hit_count:Mapped[int] = mapped_column(Integer,default=1,nullable=False)
    created_at:Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )