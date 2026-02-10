"""Integration tests for Modal backend.

These tests require Modal authentication and are marked with
@pytest.mark.modal_integration. They execute real Modal sandboxes and verify
the full integration. Run with: pytest -m modal_integration
Skip with: pytest -m "not modal_integration"

NOTE: These tests will incur actual Modal costs when run.
"""

import asyncio
import os

import pytest

from kilntainers.backends.base import ExecRequest
from kilntainers.backends.modal import (
    ModalBackend,
    ModalBackendConfig,
    ModalSandbox,
)
from kilntainers.errors import BackendError, SandboxDiedError


def _modal_auth_available() -> bool:
    """Check if Modal authentication is configured."""
    # Check environment variables
    if os.getenv("MODAL_TOKEN_ID") and os.getenv("MODAL_TOKEN_SECRET"):
        return True

    # Try to validate with Modal client (may fail if no auth)
    try:
        # Modal will check for auth token

        # Just import check - actual validation happens in tests
        return True
    except Exception:
        return False


skip_without_modal = pytest.mark.skipif(
    not _modal_auth_available(),
    reason="Modal credentials not configured. Set MODAL_TOKEN_ID and MODAL_TOKEN_SECRET, or run 'modal token set'",
)


@pytest.fixture
async def modal_backend():
    """Create a real Modal backend instance for testing.

    Automatically skips if Modal authentication is not configured.
    """
    config = ModalBackendConfig()
    backend = ModalBackend(config)
    try:
        await backend.validate()
    except BackendError as e:
        if "authentication failed" in str(e).lower():
            pytest.skip("Modal authentication failed")
        if "Cannot connect to Modal" in str(e):
            pytest.skip("Cannot connect to Modal API")
        raise
    return backend


@pytest.fixture
async def sandbox(modal_backend):
    """Create a real Modal sandbox for testing.

    The sandbox is automatically stopped after the test.
    """
    sb = await modal_backend.create_sandbox()
    yield sb
    # Cleanup: stop the sandbox
    await sb.stop()


# --- Connection Tests ---


class TestConnection:
    """Tests for Modal connection configuration."""

    @pytest.mark.modal_integration
    @pytest.mark.asyncio
    @skip_without_modal
    async def test_backend_validation_succeeds(self):
        """Backend validation succeeds with valid auth."""
        config = ModalBackendConfig()
        backend = ModalBackend(config)
        # Should not raise
        await backend.validate()

    @pytest.mark.modal_integration
    @pytest.mark.asyncio
    @skip_without_modal
    async def test_invalid_token_fails(self):
        """Invalid token causes validation to fail with BackendError.

        NOTE: This test is skipped when default Modal auth is configured,
        as Modal SDK falls back to default auth when explicit tokens are invalid.
        """
        # Skip if user has default Modal auth configured (e.g., via modal token new)
        # The SDK falls back to default auth, making this test unreliable
        if not os.getenv("MODAL_TOKEN_ID") and not os.getenv("MODAL_TOKEN_SECRET"):
            pytest.skip(
                "Cannot test invalid tokens when default Modal auth is configured. "
                "Set MODAL_TOKEN_ID and MODAL_TOKEN_SECRET to test explicit auth."
            )

        # Temporarily clear and set invalid tokens
        original_token_id = os.environ.get("MODAL_TOKEN_ID")
        original_token_secret = os.environ.get("MODAL_TOKEN_SECRET")

        try:
            os.environ["MODAL_TOKEN_ID"] = "invalid_token_id"
            os.environ["MODAL_TOKEN_SECRET"] = "invalid_token_secret"

            # Create new backend with invalid auth
            config = ModalBackendConfig(
                token_id="invalid_token_id",
                token_secret="invalid_token_secret",
            )
            backend = ModalBackend(config)

            with pytest.raises(BackendError) as exc_info:
                await backend.validate()

            assert "authentication failed" in str(exc_info.value).lower()
        finally:
            # Restore original values
            if original_token_id is not None:
                os.environ["MODAL_TOKEN_ID"] = original_token_id
            else:
                os.environ.pop("MODAL_TOKEN_ID", None)
            if original_token_secret is not None:
                os.environ["MODAL_TOKEN_SECRET"] = original_token_secret
            else:
                os.environ.pop("MODAL_TOKEN_SECRET", None)


# --- Lifecycle Tests ---


class TestLifecycle:
    """Tests for sandbox lifecycle operations."""

    @pytest.mark.modal_integration
    @pytest.mark.asyncio
    @skip_without_modal
    async def test_create_sandbox(self, modal_backend):
        """Sandbox creation succeeds."""
        sb = await modal_backend.create_sandbox()
        assert isinstance(sb, ModalSandbox)
        assert sb.sandbox_id is not None
        assert len(sb.sandbox_id) > 0
        await sb.stop()

    @pytest.mark.modal_integration
    @pytest.mark.asyncio
    @skip_without_modal
    async def test_readiness_check(self, sandbox):
        """Sandbox passes readiness check (implicit in creation)."""
        # If this test runs, readiness check passed
        assert sandbox.sandbox_id is not None

    @pytest.mark.modal_integration
    @pytest.mark.asyncio
    @skip_without_modal
    async def test_stop(self, sandbox):
        """Stop terminates the sandbox."""
        await sandbox.stop()
        assert sandbox._stopped

    @pytest.mark.modal_integration
    @pytest.mark.asyncio
    @skip_without_modal
    async def test_stop_idempotent(self, sandbox):
        """Stop can be called multiple times safely."""
        await sandbox.stop()
        await sandbox.stop()
        await sandbox.stop()
        assert sandbox._stopped


# --- Basic Exec Tests ---


class TestBasicExec:
    """Tests for basic command execution."""

    @pytest.mark.modal_integration
    @pytest.mark.asyncio
    @skip_without_modal
    async def test_echo_success(self, sandbox):
        """Simple echo command works."""
        request = ExecRequest(command="echo hello world", timeout=5, output_limit=1024)
        result = await sandbox.exec(request)
        assert result.exit_code == 0
        assert "hello world" in result.stdout
        assert result.stderr == ""

    @pytest.mark.modal_integration
    @pytest.mark.asyncio
    @skip_without_modal
    async def test_false_fails(self, sandbox):
        """Command that fails returns non-zero exit."""
        request = ExecRequest(command="false", timeout=5, output_limit=1024)
        result = await sandbox.exec(request)
        assert result.exit_code == 1

    @pytest.mark.modal_integration
    @pytest.mark.asyncio
    @skip_without_modal
    async def test_nonexistent_file(self, sandbox):
        """Nonexistent file produces error output."""
        request = ExecRequest(command="ls /nonexistent", timeout=5, output_limit=1024)
        result = await sandbox.exec(request)
        assert result.exit_code != 0
        assert "No such file" in result.stderr


# --- Command vs Args Mode Tests ---


class TestCommandVsArgsMode:
    """Tests for command mode vs args mode."""

    @pytest.mark.modal_integration
    @pytest.mark.asyncio
    @skip_without_modal
    async def test_command_mode_shell_features(self, sandbox):
        """Command mode supports shell features like pipes and redirects."""
        request = ExecRequest(
            command="echo hello | tr a-z A-Z", timeout=5, output_limit=1024
        )
        result = await sandbox.exec(request)
        assert result.exit_code == 0
        assert "HELLO" in result.stdout

    @pytest.mark.modal_integration
    @pytest.mark.asyncio
    @skip_without_modal
    async def test_args_mode_no_shell(self, sandbox):
        """Args mode does not use shell interpretation."""
        # Args mode runs echo directly, treating "|", "tr", etc. as arguments
        request = ExecRequest(
            args=["echo", "hello | tr a-z A-Z"], timeout=5, output_limit=1024
        )
        result = await sandbox.exec(request)
        assert result.exit_code == 0
        assert "hello | tr a-z A-Z" in result.stdout  # Literal output


# --- Working Directory Tests ---


class TestWorkingDirectory:
    """Tests for working_directory parameter."""

    @pytest.mark.modal_integration
    @pytest.mark.asyncio
    @skip_without_modal
    async def test_working_directory(self, sandbox):
        """Working directory changes execution context."""
        request = ExecRequest(
            command="pwd", working_directory="/tmp", timeout=5, output_limit=1024
        )
        result = await sandbox.exec(request)
        assert result.exit_code == 0
        assert "/tmp" in result.stdout

    @pytest.mark.modal_integration
    @pytest.mark.asyncio
    @skip_without_modal
    async def test_working_directory_with_command(self, sandbox):
        """Working directory works with complex commands."""
        request = ExecRequest(
            command="pwd && ls", working_directory="/etc", timeout=5, output_limit=1024
        )
        result = await sandbox.exec(request)
        assert result.exit_code == 0
        assert "/etc" in result.stdout


# --- Stdin Tests ---


class TestStdin:
    """Tests for stdin parameter."""

    @pytest.mark.modal_integration
    @pytest.mark.asyncio
    @skip_without_modal
    async def test_stdin_piping(self, sandbox):
        """Stdin data is piped to command."""
        request = ExecRequest(
            command="cat", stdin="hello from stdin", timeout=5, output_limit=1024
        )
        result = await sandbox.exec(request)
        assert result.exit_code == 0
        assert "hello from stdin" in result.stdout

    @pytest.mark.modal_integration
    @pytest.mark.asyncio
    @skip_without_modal
    async def test_stdin_special_characters(self, sandbox):
        """Stdin handles special characters correctly."""
        special_input = "Hello\nWorld\t!"
        request = ExecRequest(
            command="cat", stdin=special_input, timeout=5, output_limit=1024
        )
        result = await sandbox.exec(request)
        assert result.exit_code == 0
        # Verify newline and tab are preserved in round-trip
        assert "Hello\nWorld" in result.stdout
        assert "\t!" in result.stdout


# --- Filesystem E2E Tests ---


class TestFilesystemE2E:
    """End-to-end tests: filesystem state persists across execs in same sandbox."""

    @pytest.mark.modal_integration
    @pytest.mark.asyncio
    @skip_without_modal
    async def test_stdin_to_file_then_cat(self, sandbox):
        """Write stdin to a file in one exec; read it back in the next."""
        content = "e2e filesystem content\nline two"
        write_request = ExecRequest(
            command="cat > /tmp/e2e_content",
            stdin=content,
            timeout=5,
            output_limit=1024,
        )
        write_result = await sandbox.exec(write_request)
        assert write_result.exit_code == 0

        read_request = ExecRequest(
            command="cat /tmp/e2e_content", timeout=5, output_limit=1024
        )
        read_result = await sandbox.exec(read_request)
        assert read_result.exit_code == 0
        assert read_result.stdout == content

    @pytest.mark.modal_integration
    @pytest.mark.asyncio
    @skip_without_modal
    async def test_mkdir_then_ls(self, sandbox):
        """Create a directory in one exec; list it in the next."""
        dir_path = "/tmp/e2e_testdir"
        mkdir_request = ExecRequest(
            command=f"mkdir {dir_path}", timeout=5, output_limit=1024
        )
        mkdir_result = await sandbox.exec(mkdir_request)
        assert mkdir_result.exit_code == 0

        ls_request = ExecRequest(
            command=f"ls -d {dir_path}", timeout=5, output_limit=1024
        )
        ls_result = await sandbox.exec(ls_request)
        assert ls_result.exit_code == 0
        assert dir_path in ls_result.stdout or "e2e_testdir" in ls_result.stdout


# --- Timeout Tests ---


class TestTimeout:
    """Tests for timeout enforcement."""

    @pytest.mark.modal_integration
    @pytest.mark.asyncio
    @skip_without_modal
    async def test_timeout_exceeded(self, sandbox):
        """Long-running command is terminated after timeout."""
        request = ExecRequest(command="sleep 60", timeout=1, output_limit=1024)
        result = await sandbox.exec(request)
        # Modal returns -1 for timeout (different from Docker's 124)
        # Note: Modal's native timeout doesn't populate stderr, so we only check exit code
        assert result.exit_code == -1


# --- Output Limit Tests ---


class TestOutputLimit:
    """Tests for output_limit enforcement."""

    @pytest.mark.modal_integration
    @pytest.mark.asyncio
    @skip_without_modal
    async def test_output_limit_exceeded(self, sandbox):
        """Command generating excessive output is terminated."""
        # Use yes to generate infinite output
        request = ExecRequest(command="yes", timeout=5, output_limit=1024)
        result = await sandbox.exec(request)
        assert result.exit_code == 1
        assert "output limit exceeded" in result.stderr
        assert result.stdout == ""


# --- Stateless Execution Tests ---


class TestStatelessExecution:
    """Tests for state isolation between exec calls."""

    @pytest.mark.modal_integration
    @pytest.mark.asyncio
    @skip_without_modal
    async def test_exports_dont_persist(self, sandbox):
        """Shell variable exports don't persist across calls."""
        # Set a variable
        request1 = ExecRequest(
            command="export MY_VAR=test && echo $MY_VAR",
            timeout=5,
            output_limit=1024,
        )
        result1 = await sandbox.exec(request1)
        assert "test" in result1.stdout

        # Variable should not persist
        request2 = ExecRequest(command="echo $MY_VAR", timeout=5, output_limit=1024)
        result2 = await sandbox.exec(request2)
        assert "test" not in result2.stdout

    @pytest.mark.modal_integration
    @pytest.mark.asyncio
    @skip_without_modal
    async def test_cd_doesnt_persist(self, sandbox):
        """Directory changes don't persist across calls."""
        # Change directory
        request1 = ExecRequest(command="cd /tmp && pwd", timeout=5, output_limit=1024)
        result1 = await sandbox.exec(request1)
        assert "/tmp" in result1.stdout

        # Should be back in original directory
        request2 = ExecRequest(command="pwd", timeout=5, output_limit=1024)
        result2 = await sandbox.exec(request2)
        assert "/" in result2.stdout or "/root" in result2.stdout
        assert "/tmp" not in result2.stdout


# --- Network Isolation Tests ---


class TestNetworkIsolation:
    """Tests for network isolation behavior."""

    @pytest.mark.modal_integration
    @pytest.mark.asyncio
    @skip_without_modal
    async def test_network_disabled_by_default(self, sandbox):
        """Network access is blocked by default."""
        # Use Python to test network (no curl required)
        request = ExecRequest(
            command="python3 -c 'import urllib.request; urllib.request.urlopen(\"http://example.com\", timeout=2)'",
            timeout=5,
            output_limit=1024,
        )
        result = await sandbox.exec(request)
        # Should fail because network is disabled
        assert result.exit_code != 0

    @pytest.mark.modal_integration
    @pytest.mark.asyncio
    @skip_without_modal
    async def test_network_enabled_works(self):
        """Network access works when enabled."""
        config = ModalBackendConfig(network_enabled=True)
        backend = ModalBackend(config)
        sb = await backend.create_sandbox()
        try:
            # Use Python to test network (no curl required)
            request = ExecRequest(
                command="python3 -c 'import urllib.request; print(urllib.request.urlopen(\"http://example.com\", timeout=5).read().decode()[:100])'",
                timeout=10,
                output_limit=1024,
            )
            result = await sb.exec(request)
            # Should succeed (or at least reach the server)
            assert result.exit_code == 0
            assert (
                "Example Domain" in result.stdout or "example" in result.stdout.lower()
            )
        finally:
            await sb.stop()


# --- Exec Lock Tests ---


class TestExecLock:
    """Tests for exec serialization via lock."""

    @pytest.mark.modal_integration
    @pytest.mark.asyncio
    @skip_without_modal
    async def test_concurrent_execs_are_serialized(self, sandbox):
        """Concurrent exec calls are properly serialized."""
        # Fire multiple exec requests concurrently
        requests = [
            ExecRequest(command=f"echo {i}", timeout=5, output_limit=1024)
            for i in range(5)
        ]

        async def run_exec(req):
            return await sandbox.exec(req)

        results = await asyncio.gather(*[run_exec(req) for req in requests])

        # All should succeed
        assert len(results) == 5
        for result in results:
            assert result.exit_code == 0


# --- Death Detection Tests ---


class TestDeathDetection:
    """Tests for unexpected sandbox death detection."""

    @pytest.mark.modal_integration
    @pytest.mark.asyncio
    @skip_without_modal
    async def test_wait_for_death_blocks_on_normal_stop(self, sandbox):
        """wait_for_death blocks when stop() is called (expected death)."""
        # Start the death detection task
        death_task = asyncio.create_task(sandbox.wait_for_death())

        # Give it a moment to start
        await asyncio.sleep(0.1)

        # Stop the sandbox
        await sandbox.stop()

        # The task should still be running (waiting forever)
        # Cancel it to clean up
        death_task.cancel()
        try:
            await death_task
        except asyncio.CancelledError:
            pass  # Expected

    @pytest.mark.modal_integration
    @pytest.mark.asyncio
    @skip_without_modal
    async def test_exec_after_stop_raises_error(self, sandbox):
        """Exec raises SandboxDiedError when called after stop()."""
        await sandbox.stop()

        request = ExecRequest(command="echo test", timeout=5, output_limit=1024)
        with pytest.raises(SandboxDiedError) as exc_info:
            await sandbox.exec(request)
        assert "stopped" in str(exc_info.value).lower()


# --- Tool Instructions Tests ---


class TestToolInstructions:
    """Tests for tool_instructions method."""

    @pytest.mark.modal_integration
    @pytest.mark.asyncio
    @skip_without_modal
    async def test_tool_instructions_default_image(self):
        """Default image returns tool instructions."""
        config = ModalBackendConfig()
        backend = ModalBackend(config)
        await backend.validate()
        instructions = backend.tool_instructions()
        assert instructions is not None
        assert "bash" in instructions
        assert "120" in instructions  # default timeout

    @pytest.mark.modal_integration
    @pytest.mark.asyncio
    @skip_without_modal
    async def test_tool_instructions_custom_image(self):
        """Custom image returns None for tool instructions."""
        config = ModalBackendConfig(image="alpine:latest")
        backend = ModalBackend(config)
        await backend.validate()
        instructions = backend.tool_instructions()
        assert instructions is None


# --- Custom Shell Tests ---


class TestCustomShell:
    """Tests for custom shell configuration."""

    @pytest.mark.modal_integration
    @pytest.mark.asyncio
    @skip_without_modal
    async def test_custom_sh_shell(self):
        """Sandbox works with /bin/sh shell."""
        config = ModalBackendConfig(shell="/bin/sh")
        backend = ModalBackend(config)
        sb = await backend.create_sandbox()
        try:
            request = ExecRequest(command="echo hello", timeout=5, output_limit=1024)
            result = await sb.exec(request)
            assert result.exit_code == 0
            assert "hello" in result.stdout
        finally:
            await sb.stop()
