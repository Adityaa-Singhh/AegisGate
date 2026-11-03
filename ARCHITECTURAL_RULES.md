# AegisGate: System Context & Architectural Blueprint

AegisGate is a production-grade, high-concurrency (1,000+ req/sec) **Multi-Provider LLM Gateway & Distributed Webhook Delivery Engine** built in a single modular Python monorepo.

---

## 1. System Architecture: Two Core Modules

### Module 1: Synchronous Path — Multi-Provider LLM Gateway & Semantic Router (`src/gateway/`)
An infrastructure proxy sitting between client applications and upstream LLM providers (OpenAI, Anthropic, Groq, OpenRouter, and a built-in Chaos Mock LLM server).
- **Unified SSE Streaming Proxy:** Uses a global `httpx.AsyncClient` connection pool and FastAPI `StreamingResponse` (`text/event-stream`) to normalize streaming chunks into an OpenAI-compatible format.
- **Pre-Flight Failover & Circuit Breaker:** Verifies upstream HTTP status codes *before* committing to the `StreamingResponse` generator. Tracks provider error rates (`429`, `5xx`, timeouts) in Redis; trips circuit to `OPEN` on threshold breaches and seamlessly fails over to backup providers without dropping the client's request.
- **Semantic Vector Caching:** Uses `FastEmbed` (`BAAI/bge-small-en-v1.5`) offloaded via `asyncio.to_thread` to embed incoming prompts locally on CPU, querying PostgreSQL `pgvector` via cosine similarity (`<=>`). If similarity > 0.92, streams the cached response immediately (`X-Cache: HIT`).
- **Atomic Token Rate Limiting:** Uses Redis Lua scripts to enforce sliding-window or token-bucket limits based on **Tokens Per Minute (TPM)** rather than simple request counts, reconciled asynchronously via FastAPI `BackgroundTasks`.

### Module 2: Asynchronous Path — Resilient Webhook Delivery & Batch Engine (`src/webhooks/`)
Handles long-running async batch completions (`POST /v1/batch/completions`) and system events (e.g., tenant crossing 80% token budget alerts) with guaranteed webhook delivery to external client servers.
- **High-Throughput Ingestion & Idempotency:** Validates incoming batch/event payloads, enforces idempotency via `Idempotency-Key` headers in Redis/Postgres, persists job state as `PENDING`, enqueues to Redis via `ARQ`, and returns `202 Accepted` in under 10ms.
- **Async Worker Pool (`ARQ`):** Processes background LLM jobs and dispatches webhook POST requests to client `callback_url` endpoints with strict timeouts.
- **Cryptographic Security:** Signs all outgoing webhook payloads using `HMAC-SHA256(webhook_secret, timestamp + "." + payload)` attached in the `X-Aegis-Signature` header to prevent replay attacks.
- **Exponential Backoff with Jitter & DLQ:** Retries failed (`5xx` / timeout) webhook deliveries up to 5 times using $t = 2^{\text{attempt}} + \text{uniform}(0, 1)$ seconds. After 5 failures, transitions the event to a `DEAD_LETTER` state and exposes a `POST /v1/webhooks/{id}/replay` endpoint for manual bulk replay.

---

## 2. Strict Tech Stack & Library Constraints

Write modern Python 3.11+ using **strictly asynchronous, non-blocking patterns**:
- **Framework:** `FastAPI` + `Uvicorn` (with `uvloop`) + `Pydantic v2` (`pydantic-settings`).
- **Database:** `PostgreSQL 16` with `pgvector` extension, accessed *exclusively* via `SQLAlchemy 2.0` (`AsyncEngine`, `AsyncSession`, `async_sessionmaker`, `Mapped`, `mapped_column`) and `asyncpg`.
- **Migrations:** `Alembic` configured for async SQLAlchemy.
- **Cache & Broker:** `Redis 7` using `redis.asyncio` connection pools.
- **Task Queue:** `ARQ` (async-native Redis queue) — do NOT use synchronous Celery.
- **HTTP Client:** `httpx.AsyncClient` initialized once in FastAPI's `lifespan` context manager with explicit connection limits (`max_keepalive_connections`, `max_connections`).
- **Observability:** `structlog` for JSON-structured logging with end-to-end `X-Request-ID` correlation across FastAPI routes and ARQ worker jobs, plus `prometheus-fastapi-instrumentator`.
- **Testing & Benchmarks:** `pytest-asyncio` for unit/integration tests and `Locust` for load testing.

---

## 3. Non-Negotiable Engineering Rules

1. **Zero Event Loop Blocking:** Never use synchronous libraries (`requests`, `psycopg2`, `time.sleep`). Any CPU-bound operation (such as local `FastEmbed` vector generation or heavy payload serialization) MUST be wrapped in `await asyncio.to_thread(...)`.
2. **Pre-Flight Stream Verification:** Never place the initial upstream `httpx` request inside the `StreamingResponse` generator without first awaiting the response headers/status code. Catch `429`/`5xx`/`httpx.RequestError` *before* returning `StreamingResponse` so the gateway can cleanly failover to the secondary provider.
3. **No DB Connections Held Across Network I/O:** In `ARQ` background workers and FastAPI routes, NEVER keep an `AsyncSession` transaction open while awaiting an external `httpx` LLM or webhook network call. Fetch state -> close/commit DB session -> execute external HTTP call -> open a fresh DB session to record delivery status.
4. **Atomic Redis Operations:** Never implement rate limiting or circuit-breaker state transitions using Python-level "read-then-write" (`GET` followed by `INCRBY`). Always use atomic Redis Lua scripts or pipelines to prevent race conditions under high concurrency.
5. **Security by Default:** Never store raw tenant API keys in PostgreSQL; store only `SHA-256` hashes (`api_key_hash`) and validate via FastAPI `Security(HTTPBearer())` dependencies.

---

## 4. Repository Structure Reference

```text
aegis-platform/
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml / requirements.txt
├── alembic/
├── locustfile.py
├── ARCHITECTURAL_RULES.md
└── src/
    ├── main.py                 # FastAPI app & lifespan (httpx + Redis + DB pools)
    ├── config.py               # Pydantic BaseSettings
    ├── database.py             # SQLAlchemy 2.0 AsyncEngine & get_db
    ├── redis_client.py         # redis.asyncio pool
    ├── models.py               # Tenant, UsageLog, SemanticCache, WebhookJob, DeliveryAttempt
    ├── core/
    │   ├── auth.py             # SHA-256 API key validation
    │   ├── logger.py           # structlog + correlation ID middleware
    │   └── exceptions.py
    ├── gateway/
    │   ├── router.py           # /v1/chat/completions (streaming + failover)
    │   ├── mock_provider.py    # Chaos Mock LLM SSE server for local load testing
    │   ├── semantic_cache.py   # FastEmbed + pgvector cosine search
    │   ├── rate_limiter.py     # Redis Lua script TPM limiter
    │   └── circuit_breaker.py  # Redis-backed state machine (CLOSED/OPEN/HALF-OPEN)
    └── webhooks/
        ├── router.py           # /v1/batch/completions & DLQ /replay endpoints
        ├── producer.py         # Idempotency check + ARQ job enqueue
        ├── worker.py           # ARQ worker with exponential backoff + jitter
        └── signer.py           # HMAC-SHA256 signature generator
```
