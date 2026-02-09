"""Integration tests for Docker backend.

These tests require a running Docker daemon and are marked with
@pytest.mark.docker_integration. They execute real Docker containers
and verify the full integration.

Run with: pytest -m docker_integration
Skip with: pytest -m "not docker_integration"
"""

import asyncio

import pytest

from kilntainers.backends.base import ExecRequest
from kilntainers.backends.docker import DockerBackend, DockerSandbox
from kilntainers.config import DockerBackendConfig
from kilntainers.errors import SandboxDiedError


@pytest.fixture
async def docker_backend():
    """Create a real Docker backend instance."""
    config = DockerBackendConfig()
    backend = DockerBackend(config)
    await backend.validate()
    return backend


@pytest.fixture
async def sandbox(docker_backend):
    """Create a real Docker sandbox for testing.

    The sandbox is automatically stopped after the test.
    """
    sb = await docker_backend.create_sandbox()
    yield sb
    # Cleanup: stop the sandbox
    await sb.stop()


# --- Lifecycle Tests ---


class TestLifecycle:
    """Tests for sandbox lifecycle operations."""

    @pytest.mark.docker_integration
    @pytest.mark.asyncio
    async def test_create_sandbox(self, docker_backend):
        """Sandbox creation succeeds."""
        sb = await docker_backend.create_sandbox()
        assert isinstance(sb, DockerSandbox)
        assert sb.sandbox_id is not None
        assert len(sb.sandbox_id) == 12
        await sb.stop()

    @pytest.mark.docker_integration
    @pytest.mark.asyncio
    async def test_readiness_check(self, sandbox):
        """Sandbox passes readiness check (implicit in creation)."""
        # If this test runs, readiness check passed
        assert sandbox.sandbox_id is not None

    @pytest.mark.docker_integration
    @pytest.mark.asyncio
    async def test_stop(self, sandbox):
        """Stop terminates the sandbox."""
        await sandbox.stop()
        assert sandbox._stopped

    @pytest.mark.docker_integration
    @pytest.mark.asyncio
    async def test_stop_idempotent(self, sandbox):
        """Stop can be called multiple times safely."""
        await sandbox.stop()
        await sandbox.stop()
        await sandbox.stop()
        assert sandbox._stopped


# --- Basic Exec Tests ---


class TestBasicExec:
    """Tests for basic command execution."""

    @pytest.mark.docker_integration
    @pytest.mark.asyncio
    async def test_echo_success(self, sandbox):
        """Simple echo command works."""
        request = ExecRequest(command="echo hello world", timeout=5, output_limit=1024)
        result = await sandbox.exec(request)
        assert result.exit_code == 0
        assert "hello world" in result.stdout
        assert result.stderr == ""

    @pytest.mark.docker_integration
    @pytest.mark.asyncio
    async def test_false_fails(self, sandbox):
        """Command that fails returns non-zero exit."""
        request = ExecRequest(command="false", timeout=5, output_limit=1024)
        result = await sandbox.exec(request)
        assert result.exit_code == 1

    @pytest.mark.docker_integration
    @pytest.mark.asyncio
    async def test_nonexistent_file(self, sandbox):
        """Nonexistent file produces error output."""
        request = ExecRequest(command="ls /nonexistent", timeout=5, output_limit=1024)
        result = await sandbox.exec(request)
        assert result.exit_code != 0
        assert "No such file" in result.stderr


# --- Command vs Args Mode Tests ---


class TestCommandVsArgsMode:
    """Tests for command mode vs args mode."""

    @pytest.mark.docker_integration
    @pytest.mark.asyncio
    async def test_command_mode_shell_features(self, sandbox):
        """Command mode supports shell features like pipes and redirects."""
        request = ExecRequest(
            command="echo hello | tr a-z A-Z", timeout=5, output_limit=1024
        )
        result = await sandbox.exec(request)
        assert result.exit_code == 0
        assert "HELLO" in result.stdout

    @pytest.mark.docker_integration
    @pytest.mark.asyncio
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

    @pytest.mark.docker_integration
    @pytest.mark.asyncio
    async def test_working_directory(self, sandbox):
        """Working directory changes execution context."""
        request = ExecRequest(
            command="pwd", working_directory="/tmp", timeout=5, output_limit=1024
        )
        result = await sandbox.exec(request)
        assert result.exit_code == 0
        assert "/tmp" in result.stdout

    @pytest.mark.docker_integration
    @pytest.mark.asyncio
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

    @pytest.mark.docker_integration
    @pytest.mark.asyncio
    async def test_stdin_piping(self, sandbox):
        """Stdin data is piped to command."""
        request = ExecRequest(
            command="cat", stdin="hello from stdin", timeout=5, output_limit=1024
        )
        result = await sandbox.exec(request)
        assert result.exit_code == 0
        assert "hello from stdin" in result.stdout

    @pytest.mark.docker_integration
    @pytest.mark.asyncio
    async def test_stdin_special_characters(self, sandbox):
        """Stdin handles special characters correctly."""
        special_input = "Hello\nWorld\t!\0"
        request = ExecRequest(
            command="cat", stdin=special_input, timeout=5, output_limit=1024
        )
        result = await sandbox.exec(request)
        assert result.exit_code == 0
        # Verify newline and tab are preserved in round-trip
        assert "Hello\nWorld" in result.stdout
        assert "\t!" in result.stdout
        # Null may be stripped by capture; at least check the rest matches
        assert result.stdout.startswith("Hello\nWorld\t!")


# --- Filesystem E2E Tests ---


class TestFilesystemE2E:
    """End-to-end tests: filesystem state persists across execs in same sandbox."""

    @pytest.mark.docker_integration
    @pytest.mark.asyncio
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

    @pytest.mark.docker_integration
    @pytest.mark.asyncio
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

    @pytest.mark.docker_integration
    @pytest.mark.asyncio
    async def test_timeout_exceeded(self, sandbox):
        """Long-running command is terminated after timeout."""
        request = ExecRequest(command="sleep 60", timeout=1, output_limit=1024)
        result = await sandbox.exec(request)
        assert result.exit_code == 124
        assert "timed out" in result.stderr


# --- Output Limit Tests ---


class TestOutputLimit:
    """Tests for output_limit enforcement."""

    @pytest.mark.docker_integration
    @pytest.mark.asyncio
    async def test_output_limit_exceeded(self, sandbox):
        """Command generating excessive output is terminated."""
        # 'yes' prints 'y' infinitely
        request = ExecRequest(command="yes", timeout=5, output_limit=1024)
        result = await sandbox.exec(request)
        assert result.exit_code == 1
        assert "output limit exceeded" in result.stderr
        assert result.stdout == ""


# --- Stateless Execution Tests ---


class TestStatelessExecution:
    """Tests for state isolation between exec calls."""

    @pytest.mark.docker_integration
    @pytest.mark.asyncio
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

    @pytest.mark.docker_integration
    @pytest.mark.asyncio
    async def test_cd_doesnt_persist(self, sandbox):
        """Directory changes don't persist across calls."""
        # Change directory
        request1 = ExecRequest(command="cd /tmp && pwd", timeout=5, output_limit=1024)
        result1 = await sandbox.exec(request1)
        assert "/tmp" in result1.stdout

        # Should be back in original directory (/ for default container)
        request2 = ExecRequest(command="pwd", timeout=5, output_limit=1024)
        result2 = await sandbox.exec(request2)
        assert "/" in result2.stdout or "/root" in result2.stdout
        assert "/tmp" not in result2.stdout


# --- Network Isolation Tests ---


class TestNetworkIsolation:
    """Tests for network isolation behavior."""

    @pytest.mark.docker_integration
    @pytest.mark.asyncio
    async def test_network_disabled_by_default(self, sandbox):
        """Network access is blocked by default."""
        request = ExecRequest(
            command="curl -s --connect-timeout 2 http://example.com",
            timeout=5,
            output_limit=1024,
        )
        result = await sandbox.exec(request)
        # Should fail because network is disabled
        assert result.exit_code != 0

    @pytest.mark.docker_integration
    @pytest.mark.asyncio
    async def test_network_enabled_works(self, docker_backend):
        """Network access works when enabled."""
        config = DockerBackendConfig(network_enabled=True)
        backend = DockerBackend(config)
        sb = await backend.create_sandbox()
        try:
            request = ExecRequest(
                command="curl -s --connect-timeout 2 http://example.com",
                timeout=5,
                output_limit=1024,
            )
            result = await sb.exec(request)
            # Should succeed (or at least reach the server)
            # We just check it doesn't fail immediately
            assert result.exit_code == 0 or "curl" in result.stderr
        finally:
            await sb.stop()


# --- Death Detection Tests ---


class TestDeathDetection:
    """Tests for unexpected sandbox death detection."""

    @pytest.mark.docker_integration
    @pytest.mark.asyncio
    async def test_wait_for_death_on_kill(self, sandbox):
        """wait_for_death returns when container is killed externally."""
        # Start the death detection task
        death_task = asyncio.create_task(sandbox.wait_for_death())

        # Give it a moment to start
        await asyncio.sleep(0.1)

        # Kill the container externally
        import subprocess

        subprocess.run(
            ["docker", "kill", sandbox._container_id],
            capture_output=True,
        )

        # wait_for_death should complete
        await asyncio.wait_for(death_task, timeout=5)

    @pytest.mark.docker_integration
    @pytest.mark.asyncio
    async def test_exec_after_container_dies(self, sandbox):
        """Exec raises SandboxDiedError when container dies during exec."""
        # Kill the container externally
        import subprocess

        subprocess.run(
            ["docker", "kill", sandbox._container_id],
            capture_output=True,
        )
        await asyncio.sleep(0.1)  # Give it time to die

        # Next exec should detect the death
        request = ExecRequest(command="echo test", timeout=5, output_limit=1024)

        with pytest.raises(SandboxDiedError) as exc_info:
            await sandbox.exec(request)
        assert "died during command execution" in str(exc_info.value)


# --- Cleanup Tests ---


class TestCleanup:
    """Tests for proper resource cleanup."""

    @pytest.mark.docker_integration
    @pytest.mark.asyncio
    async def test_container_removed_on_stop(self, sandbox):
        """Container is removed (--rm flag) when stopped."""
        container_id = sandbox._container_id

        # Verify container exists
        import subprocess

        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Running}}", container_id],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

        # Stop the sandbox
        await sandbox.stop()

        # Container should be removed (not just stopped)
        result = subprocess.run(
            ["docker", "inspect", container_id],
            capture_output=True,
        )
        assert result.returncode != 0  # Container not found

    @pytest.mark.docker_integration
    @pytest.mark.asyncio
    async def test_kilntainers_label_present(self, sandbox):
        """Container has kilntainers=true label."""
        import subprocess

        result = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                '{{index .Config.Labels "kilntainers"}}',
                sandbox._container_id,
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "true" in result.stdout


# --- Tool Instructions Tests ---


class TestToolInstructions:
    """Tests for tool_instructions method."""

    @pytest.mark.docker_integration
    @pytest.mark.asyncio
    async def test_tool_instructions_default_image(self, docker_backend):
        """Default image returns tool instructions."""
        instructions = docker_backend.tool_instructions()
        assert instructions is not None
        assert "Debian" in instructions
        assert "bash" in instructions
        assert "120" in instructions  # default timeout

    @pytest.mark.docker_integration
    @pytest.mark.asyncio
    async def test_tool_instructions_custom_image(self):
        """Custom image returns None for tool instructions."""
        config = DockerBackendConfig(image="alpine:latest")
        backend = DockerBackend(config)
        await backend.validate()
        instructions = backend.tool_instructions()
        assert instructions is None
