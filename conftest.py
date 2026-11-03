# conftest.py — project-root pytest configuration
# pytest-asyncio picks this up automatically for session-scoped async fixtures.
import pytest


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"
