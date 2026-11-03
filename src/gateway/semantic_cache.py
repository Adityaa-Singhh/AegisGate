from __future__ import annotations
import asyncio
from typing import Optional
from fastembed import TextEmbedding
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.models import SemanticCache

# 1. Module-level singleton: Loads the lightweight ONNX model into memory once.
# BAAI/bge-small-en-v1.5 outputs 384-dimensional vectors.

_embedding_model: TextEmbedding | None = None

def get_embedding_model()-> TextEmbedding:
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
    return _embedding_model

def _generate_embedding_sync(text: str) -> list[float]:
    model = get_embedding_model()
    embeddings = list(model.embed([text]))
    return embeddings[0].tolist()

async def get_embedding(text: str) -> list[float]:
    """Non-blocking async wrapper.
    
    Offloads the CPU-bound embedding calculation to a separate thread pool so the FastAPI asyncio event loop never freezes.
    """
    return await asyncio.to_thread(_generate_embedding_sync,text)

async def check_semantic_cache(
        db:AsyncSession,
        prompt:str,
        model:str,
        similarity_threshold:float = 0.92,
)-> Optional[str]:
    """Looks up semantically similar prompts in PostgreSQL using pgvector.

    Cosine Distance = 1 - Cosine Similarity.
    Therefore: Cosine Distance <= (1 - similarity_threshold).
    For a 0.92 similarity threshold, distance must be <= 0.08.
    """
    prompt_embedding = await get_embedding(prompt)
    max_distance = 1.0 - similarity_threshold
    # SemanticCache.embedding.cosine_distance maps directly to the SQL '<=>' operator
    stmt = (
        select(SemanticCache)
        .where(
            SemanticCache.model == model,
            SemanticCache.embedding.cosine_distance(prompt_embedding) <= max_distance
        )
        .order_by(SemanticCache.embedding.cosine_distance(prompt_embedding))
        .limit(1)
    )
    result = await db.execute(stmt)
    cache_entry = result.scalar_one_or_none()

    if cache_entry:
        # Increment hit counter and commit
        cache_entry.hit_count +=1
        await db.commit()
        return cache_entry.response_text
    return None
async def save_to_semantic_cache(
        db:AsyncSession,
        prompt:str,
        response_text:str,
        model:str,
) -> None:
    """Stores the prompt, its vector embedding, and the completed response."""
    embedding = await get_embedding(prompt)

    cache_entry = SemanticCache(
        prompt = prompt,
        response_text = response_text,
        model = model,
        embedding = embedding,
    )
    db.add(cache_entry)
    await db.commit()
