"""End-to-end tests for E2B backend.

These tests require a valid E2B API key (set via E2B_API_KEY env var)
and are marked with @pytest.mark.integration. They create real E2B
sandboxes and verify the full integration.

Skip with: pytest -m "not integration"
"""

import os

import pytest

from kilntainers.backends.base import ExecRequest
from kilntainers.backends.e2b import (
    E2BBackend,
    E2BBackendConfig,
    E2BSandbox,
)
from kilntainers.errors import BackendError, SandboxDiedError


def is_e2b_available() -> bool:
    """Check if E2B API key is configured."""
    return os.environ.get("E2B_API_KEY") is not None


def skip_if_no_e2b():
    """Skip test if E2B is not available."""
    if not is_e2b_available():
        pytest.skip("E2B_API_KEY not set")


async def create_e2b_sandbox() -> tuple[E2BBackend, E2BSandbox]:
    """Create a fresh E2B backend and sandbox for testing."""
    skip_if_no_e2b()
    config = E2BBackendConfig()
    backend = E2BBackend(config)
    try:
        await backend.validate()
    except BackendError:
        pytest.skip("E2B API validation failed")
    sb = await backend.create_sandbox()
    return backend, sb  # type: ignore


# --- Smoke Tests ---
# These are minimal tests to verify the E2B backend works with real API.


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="class")
class TestE2BSmoke:
    """Smoke tests for E2B backend."""

    async def test_stop_and_exec_raises(self):
        """Exec after stop raises SandboxDiedError."""
        _, sb = await create_e2b_sandbox()
        await sb.stop()

        request = ExecRequest(command="echo test", timeout=30, output_limit=1024)
        with pytest.raises(SandboxDiedError):
            await sb.exec(request)


# --- Non-async tests (no event loop issues) ---


@pytest.mark.integration
class TestE2BToolInstructions:
    """Tests for tool_instructions method (no async required)."""

    def test_tool_instructions_default_template(self):
        """Default template returns tool instructions."""
        config = E2BBackendConfig()
        backend = E2BBackend(config)
        instructions = backend.tool_instructions()
        assert instructions is not None
        assert "E2B" in instructions
        assert "bash" in instructions

    def test_tool_instructions_custom_template(self):
        """Custom template returns None for tool instructions."""
        config = E2BBackendConfig(template="custom-template")
        backend = E2BBackend(config)
        instructions = backend.tool_instructions()
        assert instructions is None
