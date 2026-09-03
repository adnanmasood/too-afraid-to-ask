"""Shared pytest configuration."""

import pytest


@pytest.fixture
def anyio_backend() -> str:
    """Keep MCP/AnyIO contract tests on the installed asyncio backend."""

    return "asyncio"
