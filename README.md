# AegisGate

> **High-Concurrency Multi-Provider LLM Gateway & Distributed Webhook Engine**  
> Built with FastAPI, SQLAlchemy 2.0 (asyncpg), PostgreSQL 16 (pgvector), Redis 7, and Docker.

---

## 🌟 Overview

**AegisGate** is a production-grade backend infrastructure proxy and resilient batch delivery platform designed to showcase senior-level asynchronous system design, high concurrency (1,000+ req/sec), zero-event-loop-blocking patterns, and intelligent LLM routing.

### Key Architectural Highlights
- **Pre-Flight Stream Failover:** Upstream HTTP responses are verified *before* committing to a `StreamingResponse` generator, allowing transparent fallback if a provider returns `429`, `5xx`, or connection timeouts.
- **Semantic Vector Caching:** Prompts are converted into dense embeddings on CPU using `FastEmbed` (`BAAI/bge-small-en-v1.5`, 384-d) offloaded via `asyncio.to_thread`. Cosine similarity queries (`<=>`) against PostgreSQL `pgvector` return cached completions instantly with `X-Cache-Status: HIT`.
- **Atomic Token-Bucket Rate Limiter:** Enforces tenant Tokens-Per-Minute (TPM) limits using atomic Redis Lua scripts to eliminate concurrency race conditions.
- **Distributed Circuit Breaker:** Implements a Redis-backed state machine (`CLOSED` ➔ `OPEN` ➔ `HALF-OPEN`) to protect upstream providers from cascading failures and fail fast during outages.
- **Strict Isolation & Resource Hygiene:** Isolated background DB sessions (`AsyncSessionLocal()`) ensure database connections are never held across external HTTP or network I/O.
- **Full Observability & Security:** End-to-end `X-Request-ID` correlation middleware with structured logging (`structlog`) and constant-time SHA-256 API key verification.

---

## 🗓️ Weekly Milestones & Implementation Progress

### ✅ Week 1: Core Foundation & Pre-Flight Stream Failover
- **Async Runtime & Lifespan:** Initialized FastAPI with `lifespan` manager orchestrating connection pools: `SQLAlchemy 2.0` `AsyncEngine` (`asyncpg`), `redis.asyncio`, and a shared `httpx.AsyncClient` with custom keepalive and timeout limits.
- **Database Architecture:** `Tenant` and `UsageLog` ORM models mapped with strict type annotations (`Mapped[...]`) and timezone-aware timestamps.
- **Chaos Mock LLM Server (`/mock-llm`):** Configurable SSE mock provider capable of simulating healthy OpenAI-compatible token streaming as well as chaos scenarios (`503` service outage, `429` rate limiting, `timeout`).
- **Pre-Flight Failover Gateway (`POST /v1/chat/completions`):** Iterates over an ordered provider fallback chain (Primary ➔ Fallback). Probes HTTP status before returning `StreamingResponse`. On failure, cleanly releases the connection (`aclose()`) and routes to the secondary provider without dropping client requests.
- **Background Usage Logging:** Dispatches non-blocking async DB writes via `BackgroundTasks` with estimated token counts, latency tracking, and fallback attribution.
- **Security & Tracing:** Bearer token authentication against SHA-256 key hashes in PostgreSQL; correlation ID middleware injecting `X-Request-ID`.

### ✅ Week 2: Semantic Caching, Atomic Rate Limiting & Circuit Breaker
- **Local Embedding Engine:** Integrated `FastEmbed` (`BAAI/bge-small-en-v1.5`) offloaded to `asyncio.to_thread` to maintain a non-blocking asyncio event loop.
- **pgvector Semantic Cache:** Implemented `SemanticCache` model and vector distance matching (`cosine_distance <= 0.08` for $\ge 0.92$ similarity). Added `X-Bypass-Cache` support.
- **Atomic Lua Token Limiter:** Built `TokenRateLimiter` using an in-memory Redis Lua script enforcing sliding-window TPM allocations with atomic `INCRBY` and `EXPIRE`. Returns `X-RateLimit-Limit`, `X-RateLimit-Remaining`, and `Retry-After` headers.
- **Distributed Circuit Breaker:** Built `CircuitBreaker` state machine (`CLOSED`, `OPEN`, `HALF_OPEN`) tracking rolling failure thresholds in Redis with cooldown recovery and atomic half-open canary probe acquisition.
- **Automated Verification:** Comprehensive test suite (`pytest-asyncio`) covering cache hits/misses, TPM blocking, and circuit tripping under chaos injection.

### ⏳ Week 3: Resilient Webhook Delivery Engine (Upcoming)
- ARQ (async Redis queue) task worker setup.
- Idempotent batch completion ingestion (`POST /v1/batch/completions`).
- HMAC-SHA256 signature generation (`X-Aegis-Signature`).
- Exponential backoff with jitter and Dead Letter Queue (DLQ) replay endpoints.

---

## 🏗️ Repository Structure

```text
aegis-platform/
├── docker-compose.yml          # PostgreSQL 16 (pgvector), Redis 7, pgAdmin 4, Redis Commander
├── requirements.txt            # Pinned dependencies
├── pytest.ini                  # Pytest asyncio configuration
├── conftest.py                 # Asyncio test loop configuration
├── ARCHITECTURAL_RULES.md      # Strict non-negotiable engineering rules
├── scripts/
│   └── seed.py                 # Database seeding script (Tenants & API keys)
├── src/
│   ├── config.py               # Pydantic BaseSettings
│   ├── database.py             # AsyncEngine & async_sessionmaker
│   ├── main.py                 # FastAPI application & lifespan management
│   ├── models.py               # SQLAlchemy 2.0 ORM models (Tenant, UsageLog, SemanticCache)
│   ├── core/
│   │   ├── auth.py             # SHA-256 API key authentication
│   │   └── logger.py           # structlog & CorrelationIdMiddleware
│   └── gateway/
│       ├── circuit_breaker.py  # Redis-backed distributed circuit breaker
│       ├── mock_provider.py    # Chaos Mock LLM SSE provider
│       ├── rate_limiter.py     # Redis Lua sliding-window TPM rate limiter
│       ├── router.py           # Unified SSE gateway & failover router
│       ├── schemas.py          # Pydantic v2 validation models
│       └── semantic_cache.py   # FastEmbed & pgvector cosine similarity search
└── tests/
    ├── test_week1_failover.py  # Auth, streaming & pre-flight failover tests
    └── test_week2_features.py  # Semantic cache, rate limiter & circuit breaker tests
```

---

## 🚀 Quick Start

### 1. Prerequisites
- Python 3.11+ (Python 3.12 recommended)
- Docker Desktop with WSL 2 enabled

### 2. Start Infrastructure
Run PostgreSQL (with `pgvector`), Redis, and inspection GUIs:
```bash
docker compose up -d
```

| Service | Address | Credentials |
| :--- | :--- | :--- |
| **PostgreSQL 16** | `localhost:5432` | `aegis` / `aegis_password` (`aegis_db`) |
| **Redis 7** | `localhost:6379` | *None* |
| **pgAdmin 4** | `http://localhost:5050` | `admin@aegis.com` / `admin_password` |
| **Redis Commander** | `http://localhost:8081` | *Auto-connected* |

### 3. Setup Virtual Environment
```bash
python -m venv .venv
# On Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# On Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt
```

### 4. Seed Database
```bash
python -m scripts.seed
```
*Seeds initial tenant `Acme Corp` with API Key: `ag-live-testkey123456`.*

### 5. Run the Server
```bash
uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
```
- API Documentation (Swagger): [http://localhost:8000/docs](http://localhost:8000/docs)
- Health Check: [http://localhost:8000/health](http://localhost:8000/health)

---

## 🧪 Running Tests

Execute the full suite of asynchronous integration tests:
```bash
pytest tests/ -v
```

All 7 core test suites cover:
- ✅ Rejection of invalid / missing API keys (`401 Unauthorized`)
- ✅ SSE chunk normalization from primary LLM provider
- ✅ Pre-flight 503 failover to secondary provider without dropping client stream
- ✅ Semantic vector cache matching ($\ge 0.92$ cosine similarity)
- ✅ Cache bypass via `X-Bypass-Cache: true`
- ✅ Sliding-window TPM rate limit enforcement (`429 Too Many Requests`)
- ✅ Automatic circuit breaker tripping to `OPEN` after repeated provider failures

---

## 🛡️ Non-Negotiable Engineering Rules

1. **Zero Event Loop Blocking:** CPU-intensive routines (vector embeddings, heavy serialization) are offloaded to `asyncio.to_thread`.
2. **Pre-Flight Stream Check:** Upstream status is evaluated before opening the `StreamingResponse` body stream to guarantee clean failovers.
3. **No DB Sessions Held Across Network I/O:** DB sessions are short-lived and never kept open during external HTTP/LLM calls.
4. **Atomic Concurrency:** Rate limiting and circuit state transitions are handled exclusively through atomic Redis Lua scripts and pipelines.
5. **Security by Default:** API keys are stored solely as SHA-256 hashes; secrets are managed via environment variables.

---

## 📄 License
MIT License. Built for distributed systems engineering showcase.
