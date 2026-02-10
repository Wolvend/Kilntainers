"""Integration tests for the full server lifecycle.

These tests require a running Docker or Podman daemon and are marked with
@pytest.mark.docker_integration. They test the full server lifecycle with
real containers, not mocks.

Run with: pytest -m docker_integration
Skip with: pytest -m "not docker_integration"
"""

import asyncio
import os
import shutil
import signal
import subprocess
from typing import cast
from unittest.mock import MagicMock

import pytest

from kilntainers.backends.docker import (
    DockerBackend,
    DockerBackendConfig,
    DockerSandbox,
)
from kilntainers.config import ServerConfig
from kilntainers.errors import BackendError
from kilntainers.server import create_lifespan, create_server


@pytest.fixture(params=["docker", "podman"])
def engine(request):
    """Parameterize tests over container engine (docker, podman).

    Automatically skips if the engine CLI is not installed on the system.
    """
    engine_name = request.param
    if shutil.which(engine_name) is None:
        pytest.skip(f"{engine_name} CLI not installed")
    return engine_name


@pytest.fixture
async def backend(engine):
    """Create a real Docker/Podman backend instance for the given engine.

    Automatically skips if the engine daemon is not running.
    """
    config = DockerBackendConfig(engine=engine)
    backend_instance = DockerBackend(config)
    try:
        await backend_instance.validate()
    except BackendError as e:
        if f"Is the {engine} daemon running?" in str(e):
            pytest.skip(f"{engine} daemon not running")
        raise
    return backend_instance


@pytest.fixture
async def server_config():
    """Return a default server config for testing."""
    return ServerConfig()


# ====================
# Full Lifecycle Tests
# ====================


class TestFullLifecycle:
    """Tests for the complete server lifecycle from start to stop."""

    @pytest.mark.docker_integration
    @pytest.mark.asyncio
    async def test_lifecycle_full_stdio_session(self, backend, server_config, engine):
        """Full stdio lifecycle: start sandbox, exec commands, stop sandbox."""
        lifespan_fn = create_lifespan(backend, "stdio")
        mock_server = MagicMock()

        container_id = None

        async with lifespan_fn(mock_server) as ctx:
            # Verify sandbox was created
            assert ctx.sandbox is not None
            assert ctx.sandbox.sandbox_id is not None
            sandbox = cast(DockerSandbox, ctx.sandbox)
            container_id = sandbox._container_id

            # Verify container exists via docker/podman CLI
            result = subprocess.run(
                [engine, "inspect", "--format", "{{.State.Running}}", container_id],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0
            assert "true" in result.stdout

            # Execute a command
            from kilntainers.backends.base import ExecRequest

            request = ExecRequest(command="echo hello", timeout=5, output_limit=1024)
            result = await ctx.sandbox.exec(request)
            assert result.exit_code == 0
            assert "hello" in result.stdout

        # After lifespan exit, verify container was removed
        result = subprocess.run(
            [engine, "inspect", container_id],
            capture_output=True,
        )
        assert result.returncode != 0  # Container not found

    @pytest.mark.docker_integration
    @pytest.mark.asyncio
    async def test_lifecycle_creates_server_with_lifespan(self, backend, server_config):
        """create_server creates a FastMCP instance with configured lifespan."""
        server = create_server(backend, server_config)

        # Verify server was created
        assert server is not None
        assert server.name == "Kilntainers"

        # The lifespan is configured but we can't easily test it runs
        # without actually calling mcp.run() which blocks


# ====================
# Sandbox Creation Failure Tests
# ====================


class TestSandboxCreationFailure:
    """Tests for handling sandbox creation failures."""

    @pytest.mark.docker_integration
    @pytest.mark.asyncio
    async def test_sandbox_creation_failure_raises(self, engine):
        """BackendError during sandbox creation propagates through lifespan."""
        # First check if Docker daemon is running
        config = DockerBackendConfig(engine=engine)
        backend = DockerBackend(config)
        try:
            await backend.validate()
        except BackendError as e:
            if f"Is the {engine} daemon running?" in str(e):
                pytest.skip(f"{engine} daemon not running")
            raise

        # Now use a bad image that doesn't exist
        bad_config = DockerBackendConfig(
            engine=engine, image="nonexistent/nonexistent:latest"
        )
        bad_backend = DockerBackend(bad_config)
        # No need to validate again - we already know daemon is running

        lifespan_fn = create_lifespan(bad_backend, "stdio")
        mock_server = MagicMock()

        with pytest.raises(BackendError) as exc_info:
            async with lifespan_fn(mock_server):
                pass

        # Error should mention image pull or creation failure
        assert (
            "image" in str(exc_info.value).lower()
            or "pull" in str(exc_info.value).lower()
        )


# ====================
# Graceful Shutdown Tests
# ====================


class TestGracefulShutdown:
    """Tests for graceful shutdown behavior."""

    @pytest.mark.docker_integration
    @pytest.mark.asyncio
    async def test_lifecycle_stops_sandbox_on_exception(self, backend):
        """Sandbox is stopped even when an exception occurs in the lifespan body."""
        lifespan_fn = create_lifespan(backend, "stdio")
        mock_server = MagicMock()

        container_id = None

        with pytest.raises(ValueError):
            async with lifespan_fn(mock_server) as ctx:
                sandbox = cast(DockerSandbox, ctx.sandbox)
                container_id = sandbox._container_id
                # Raise an exception to simulate error during session
                raise ValueError("simulated error")

        # Verify container was still cleaned up
        result = subprocess.run(
            ["docker", "inspect", container_id],
            capture_output=True,
        )
        assert result.returncode != 0  # Container not found

    @pytest.mark.docker_integration
    @pytest.mark.asyncio
    async def test_lifecycle_death_task_cancelled_before_stop(self, backend):
        """Death task is cancelled before sandbox.stop() is called."""
        lifespan_fn = create_lifespan(backend, "stdio")
        mock_server = MagicMock()

        async with lifespan_fn(mock_server) as ctx:
            death_task = ctx.death_task
            assert not death_task.cancelled()
            sandbox = cast(DockerSandbox, ctx.sandbox)

        # After exit, death task should be cancelled
        assert death_task.cancelled()

        # And sandbox should be stopped
        assert sandbox._stopped


# ====================
# Death Propagation Tests
# ====================


class TestDeathPropagation:
    """Tests for sandbox death propagation."""

    @pytest.mark.docker_integration
    @pytest.mark.asyncio
    async def test_death_propagation_stdio(self, backend, monkeypatch, engine):
        """Sandbox death triggers SIGTERM in stdio mode."""
        kill_calls: list[tuple[int, int]] = []
        monkeypatch.setattr(os, "kill", lambda pid, sig: kill_calls.append((pid, sig)))

        lifespan_fn = create_lifespan(backend, "stdio")
        mock_server = MagicMock()

        async with lifespan_fn(mock_server) as ctx:
            sandbox = cast(DockerSandbox, ctx.sandbox)
            container_id = sandbox._container_id

            # Actually kill the container externally
            subprocess.run(
                [engine, "kill", container_id],
                capture_output=True,
            )

            # Give death task time to process
            await asyncio.sleep(0.2)

        # Verify SIGTERM was sent to ourselves
        assert len(kill_calls) >= 1
        # At least one call should be SIGTERM to our own PID
        sigterm_calls = [c for c in kill_calls if c[1] == signal.SIGTERM]
        assert len(sigterm_calls) >= 1

    @pytest.mark.docker_integration
    @pytest.mark.asyncio
    async def test_external_container_kill_detected(self, backend, engine):
        """Killing container externally is detected by wait_for_death()."""
        lifespan_fn = create_lifespan(backend, "stdio")
        mock_server = MagicMock()

        death_detected = False

        async def monitor_and_kill():
            """Monitor death and kill externally."""
            async with lifespan_fn(mock_server) as ctx:
                nonlocal death_detected
                sandbox = cast(DockerSandbox, ctx.sandbox)
                container_id = sandbox._container_id

                # Wait for death task to start
                await asyncio.sleep(0.1)

                # Kill the container externally
                subprocess.run(
                    [engine, "kill", container_id],
                    capture_output=True,
                )

                # Wait for death detection (with timeout)
                try:
                    await asyncio.wait_for(ctx.death_task, timeout=5)
                    death_detected = True
                except asyncio.TimeoutError:
                    pass

        await monitor_and_kill()

        # Death should have been detected
        assert death_detected


# ====================
# Cleanup Verification Tests
# ====================


class TestCleanupVerification:
    """Tests for proper resource cleanup."""

    @pytest.mark.docker_integration
    @pytest.mark.asyncio
    async def test_container_removed_after_lifespan(self, backend, engine):
        """Container is removed (--rm flag) after lifespan exits."""
        lifespan_fn = create_lifespan(backend, "stdio")
        mock_server = MagicMock()

        container_id = None

        async with lifespan_fn(mock_server) as ctx:
            sandbox = cast(DockerSandbox, ctx.sandbox)
            container_id = sandbox._container_id

            # Verify container exists
            result = subprocess.run(
                [engine, "inspect", container_id],
                capture_output=True,
            )
            assert result.returncode == 0

        # After lifespan exit, container should be removed
        result = subprocess.run(
            [engine, "inspect", container_id],
            capture_output=True,
        )
        assert result.returncode != 0

    @pytest.mark.docker_integration
    @pytest.mark.asyncio
    async def test_no_orphaned_containers_after_exception(self, backend, engine):
        """No orphaned containers after exception during lifespan."""
        # Get initial container count
        result = subprocess.run(
            [engine, "ps", "-q", "--filter", "label=kilntainers=true"],
            capture_output=True,
            text=True,
        )
        initial_count = (
            len(result.stdout.strip().split("\n")) if result.stdout.strip() else 0
        )

        config = DockerBackendConfig(engine=engine)
        test_backend = DockerBackend(config)

        lifespan_fn = create_lifespan(test_backend, "stdio")
        mock_server = MagicMock()

        try:
            with pytest.raises(ValueError):
                async with lifespan_fn(mock_server):
                    # Raise exception before explicit cleanup
                    raise ValueError("test exception")
        except Exception:
            pass

        # Check no new orphaned containers
        result = subprocess.run(
            [engine, "ps", "-q", "--filter", "label=kilntainers=true"],
            capture_output=True,
            text=True,
        )
        final_count = (
            len(result.stdout.strip().split("\n")) if result.stdout.strip() else 0
        )

        # Should have same count as before (no orphans)
        assert final_count == initial_count
