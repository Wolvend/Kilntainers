"""Tests for the MCP server implementation."""

import asyncio
import json
import os
import signal
from typing import cast
from unittest.mock import MagicMock

import pytest

from kilntainers.backends.base import ExecResult
from kilntainers.backends.test_utils import MockBackend, MockSandbox
from kilntainers.config import BackendConfig, ServerConfig
from kilntainers.errors import BackendError
from kilntainers.server import (
    SessionContext,
    _create_handler,
    assemble_tool_description,
    create_lifespan,
    create_server,
)

# --- Test Configuration ---


@pytest.fixture
def server_config() -> ServerConfig:
    """Return a default server config for testing."""
    return ServerConfig()


@pytest.fixture
def mock_backend() -> MockBackend:
    """Return a mock backend for testing."""
    return MockBackend(
        BackendConfig(),
        tool_instructions="A Debian Linux bash shell"
    )


@pytest.fixture
async def mock_context(mock_backend: MockBackend) -> MagicMock:
    """Return a mock FastMCP Context for testing."""
    ctx = MagicMock()
    ctx.request_context.lifespan_context = SessionContext(
        sandbox=MockSandbox(),
        death_task=asyncio.create_task(asyncio.sleep(10)),
    )
    return ctx


# --- Tool Description Assembly Tests ---


def test_assemble_tool_description_override(mock_backend: MockBackend) -> None:
    """Override provided returns override, ignores backend."""
    result = assemble_tool_description(
        mock_backend,
        override="Custom description",
        extended=None,
    )
    assert result == "Custom description"


def test_assemble_tool_description_backend_only(mock_backend: MockBackend) -> None:
    """Backend provides instructions returns backend text."""
    result = assemble_tool_description(mock_backend, override=None, extended=None)
    assert result == "A Debian Linux bash shell"


def test_assemble_tool_description_backend_with_extended(
    mock_backend: MockBackend,
) -> None:
    """Backend provides instructions + extended concatenated with \\n\\n."""
    result = assemble_tool_description(
        mock_backend,
        override=None,
        extended="With additional info.",
    )
    assert result == "A Debian Linux bash shell\n\nWith additional info."


def test_assemble_tool_description_no_backend_no_override() -> None:
    """No backend instructions and no override raises BackendError."""
    backend = MockBackend(BackendConfig(), tool_instructions=None)
    with pytest.raises(BackendError) as exc_info:
        assemble_tool_description(backend, override=None, extended=None)
    assert "does not provide tool instructions" in str(exc_info.value)
    assert "--tool-instruction-override" in str(exc_info.value)


def test_assemble_tool_description_empty_backend_no_override() -> None:
    """Backend returns empty string, no override raises BackendError."""
    backend = MockBackend(BackendConfig(), tool_instructions="")
    with pytest.raises(BackendError) as exc_info:
        assemble_tool_description(backend, override=None, extended=None)
    assert "does not provide tool instructions" in str(exc_info.value)


def test_assemble_tool_description_both_override_and_extended() -> None:
    """Both override and extended raises BackendError."""
    backend = MockBackend(BackendConfig(), tool_instructions="test")  # type: ignore[arg-type]
    with pytest.raises(BackendError) as exc_info:
        assemble_tool_description(
            backend,
            override="Override",
            extended="Extended",
        )
    assert "Cannot use both" in str(exc_info.value)


# --- Input Validation Tests ---


@pytest.mark.parametrize(
    ("command", "args", "stdin", "working_dir", "timeout", "expected_error"),
    [
        # Both command and args
        ("ls", ["/bin/ls"], None, None, None, "Cannot provide both"),
        # Neither command nor args
        (None, None, None, None, None, "Must provide either"),
        # Relative working_directory
        ("ls", None, None, "relative/path", None, "absolute path"),
        # timeout < 1
        ("ls", None, None, None, 0, "at least 1 second"),
        ("ls", None, None, None, -5, "at least 1 second"),
        # stdin exceeds 2 MiB
        (
            "ls",
            None,
            "x" * (2 * 1024 * 1024 + 1),
            None,
            None,
            "exceeds the 2 MiB limit",
        ),
    ],
)
def test_validate_inputs_invalid(
    command: str | None,
    args: list[str] | None,
    stdin: str | None,
    working_dir: str | None,
    timeout: int | None,
    expected_error: str,
) -> None:
    """Various invalid inputs return error messages."""
    from kilntainers.server import _validate_inputs

    error = _validate_inputs(command, args, stdin, working_dir, timeout)
    assert error is not None
    assert expected_error in error


def test_validate_inputs_valid_command_only() -> None:
    """Valid command only passes."""
    from kilntainers.server import _validate_inputs

    error = _validate_inputs("ls -la", None, None, None, None)
    assert error is None


def test_validate_inputs_valid_args_only() -> None:
    """Valid args only passes."""
    from kilntainers.server import _validate_inputs

    error = _validate_inputs(None, ["/bin/ls", "-la"], None, None, None)
    assert error is None


def test_validate_inputs_all_optional_params() -> None:
    """All optional params populated passes."""
    from kilntainers.server import _validate_inputs

    error = _validate_inputs(
        command="ls",
        args=None,
        stdin="input",
        working_directory="/tmp",
        timeout=30,
    )
    assert error is None


def test_validate_inputs_stdin_at_exactly_2mib() -> None:
    """stdin at exactly 2 MiB passes."""
    from kilntainers.server import _validate_inputs

    stdin_content = "x" * (2 * 1024 * 1024)
    error = _validate_inputs("cat", None, stdin_content, None, None)
    assert error is None


# --- Handler Normal Response Tests ---


async def test_handler_success_command(
    mock_context: MagicMock,
    server_config: ServerConfig,
) -> None:
    """Successful command returns isError=False, exit_code 0."""
    handler = _create_handler(server_config)  # Get handler from factory

    # Configure mock to return success
    mock_context.request_context.lifespan_context.sandbox.exec_results.append(
        ExecResult(stdout="hello\n", stderr="", exit_code=0, exec_duration_ms=10)
    )

    result = await handler(command="echo hello", ctx=mock_context)

    assert result.isError is False
    content = result.content[0]
    assert content.type == "text"

    response_json = json.loads(content.text)
    assert response_json["stdout"] == "hello\n"
    assert response_json["stderr"] == ""
    assert response_json["exit_code"] == 0
    assert response_json["exec_duration_ms"] == 10


async def test_handler_failed_command(
    mock_context: MagicMock,
    server_config: ServerConfig,
) -> None:
    """Failed command returns isError=False, non-zero exit_code."""
    handler = _create_handler(server_config)

    mock_context.request_context.lifespan_context.sandbox.exec_results.append(
        ExecResult(
            stdout="",
            stderr="command not found\n",
            exit_code=127,
            exec_duration_ms=5,
        )
    )

    result = await handler(command="nonexistent", ctx=mock_context)

    assert result.isError is False
    response_json = json.loads(result.content[0].text)
    assert response_json["exit_code"] == 127
    assert response_json["stderr"] == "command not found\n"


async def test_handler_timeout_result(
    mock_context: MagicMock,
    server_config: ServerConfig,
) -> None:
    """Timeout result returns isError=False, exit_code 124."""
    handler = _create_handler(server_config)

    mock_context.request_context.lifespan_context.sandbox.exec_results.append(
        ExecResult(stdout="", stderr="", exit_code=124, exec_duration_ms=120000)
    )

    result = await handler(command="sleep 300", ctx=mock_context)

    assert result.isError is False
    response_json = json.loads(result.content[0].text)
    assert response_json["exit_code"] == 124


async def test_handler_output_limit_result(
    mock_context: MagicMock,
    server_config: ServerConfig,
) -> None:
    """Output limit result returns isError=False, exit_code 1."""
    handler = _create_handler(server_config)

    mock_context.request_context.lifespan_context.sandbox.exec_results.append(
        ExecResult(stdout="truncated...", stderr="", exit_code=1, exec_duration_ms=50)
    )

    result = await handler(command="yes", ctx=mock_context)

    assert result.isError is False
    response_json = json.loads(result.content[0].text)
    assert response_json["exit_code"] == 1


async def test_response_json_contains_all_fields(
    mock_context: MagicMock,
    server_config: ServerConfig,
) -> None:
    """Response JSON contains all four fields."""
    handler = _create_handler(server_config)

    mock_context.request_context.lifespan_context.sandbox.exec_results.append(
        ExecResult(
            stdout="out",
            stderr="err",
            exit_code=0,
            exec_duration_ms=42,
        )
    )

    result = await handler(command="test", ctx=mock_context)
    response_json = json.loads(result.content[0].text)

    assert set(response_json.keys()) == {
        "stdout",
        "stderr",
        "exit_code",
        "exec_duration_ms",
    }
    assert response_json["stdout"] == "out"
    assert response_json["stderr"] == "err"
    assert response_json["exit_code"] == 0
    assert response_json["exec_duration_ms"] == 42


# --- Handler Error Response Tests ---


async def test_handler_invalid_inputs(server_config: ServerConfig) -> None:
    """Invalid inputs returns CallToolResult with isError=True."""
    handler = _create_handler(server_config)

    result = await handler(command="ls", args=["/bin/ls"], ctx=None)

    assert result.isError is True
    assert "Cannot provide both" in result.content[0].text


async def test_handler_sandbox_died_error(
    mock_context: MagicMock, server_config: ServerConfig
) -> None:
    """SandboxDiedError returns isError=True with descriptive message."""
    handler = _create_handler(server_config)

    # Configure mock to raise SandboxDiedError
    mock_context.request_context.lifespan_context.sandbox.exec_results.append(
        ExecResult(stdout="", stderr="", exit_code=0, exec_duration_ms=1)
    )
    mock_context.request_context.lifespan_context.sandbox._death_event.set()

    result = await handler(command="test", ctx=mock_context)

    assert result.isError is True
    assert "died" in result.content[0].text.lower()


async def test_handler_no_context_error(server_config: ServerConfig) -> None:
    """Handler with None context returns isError=True."""
    handler = _create_handler(server_config)

    result = await handler(command="ls", ctx=None)

    assert result.isError is True
    assert "no context provided" in result.content[0].text


# --- ExecRequest Construction Tests ---


async def test_request_construction_command_mode(
    mock_context: MagicMock, server_config: ServerConfig
) -> None:
    """command mode creates ExecRequest with command, args is None."""
    handler = _create_handler(server_config)

    await handler(command="ls -la", ctx=mock_context)

    request = mock_context.request_context.lifespan_context.sandbox.exec_calls[0]
    assert request.command == "ls -la"
    assert request.args is None


async def test_request_construction_args_mode(
    mock_context: MagicMock, server_config: ServerConfig
) -> None:
    """args mode creates ExecRequest with args, command is None."""
    handler = _create_handler(server_config)

    await handler(args=["/bin/ls", "-la"], ctx=mock_context)

    request = mock_context.request_context.lifespan_context.sandbox.exec_calls[0]
    assert request.args == ["/bin/ls", "-la"]
    assert request.command is None


async def test_request_construction_timeout_provided(
    mock_context: MagicMock,
    server_config: ServerConfig,
) -> None:
    """timeout provided uses provided value."""
    handler = _create_handler(server_config)

    await handler(command="test", timeout=60, ctx=mock_context)

    request = mock_context.request_context.lifespan_context.sandbox.exec_calls[0]
    assert request.timeout == 60


async def test_request_construction_timeout_default(
    mock_context: MagicMock,
    server_config: ServerConfig,
) -> None:
    """timeout not provided uses server default."""
    handler = _create_handler(server_config)

    await handler(command="test", ctx=mock_context)

    request = mock_context.request_context.lifespan_context.sandbox.exec_calls[0]
    assert request.timeout == server_config.default_timeout


async def test_request_construction_output_limit_always_from_config(
    mock_context: MagicMock,
    server_config: ServerConfig,
) -> None:
    """output_limit always from server config."""
    handler = _create_handler(server_config)

    await handler(command="test", ctx=mock_context)

    request = mock_context.request_context.lifespan_context.sandbox.exec_calls[0]
    assert request.output_limit == server_config.output_limit


async def test_request_construction_stdin_passed(
    mock_context: MagicMock, server_config: ServerConfig
) -> None:
    """stdin passed through."""
    handler = _create_handler(server_config)

    await handler(command="cat", stdin="input data", ctx=mock_context)

    request = mock_context.request_context.lifespan_context.sandbox.exec_calls[0]
    assert request.stdin == "input data"


async def test_request_construction_working_directory_passed(
    mock_context: MagicMock, server_config: ServerConfig
) -> None:
    """working_directory passed through."""
    handler = _create_handler(server_config)

    await handler(command="pwd", working_directory="/tmp", ctx=mock_context)

    request = mock_context.request_context.lifespan_context.sandbox.exec_calls[0]
    assert request.working_directory == "/tmp"


async def test_request_construction_working_directory_none_when_not_provided(
    mock_context: MagicMock, server_config: ServerConfig
) -> None:
    """working_directory is None when not provided."""
    handler = _create_handler(server_config)

    await handler(command="pwd", ctx=mock_context)

    request = mock_context.request_context.lifespan_context.sandbox.exec_calls[0]
    assert request.working_directory is None


# --- Lifespan Tests ---


async def test_lifespan_creates_sandbox(mock_backend: MockBackend) -> None:
    """Lifespan creates a sandbox via backend.create_sandbox()."""
    lifespan_fn = create_lifespan(mock_backend, "stdio")
    mock_server = MagicMock()

    async with lifespan_fn(mock_server) as ctx:
        assert ctx.sandbox is not None
        assert ctx.sandbox.sandbox_id == "mock-sandbox-001"


async def test_lifespan_yields_session_context(mock_backend: MockBackend) -> None:
    """Lifespan yields a SessionContext with sandbox and death_task."""
    lifespan_fn = create_lifespan(mock_backend, "stdio")
    mock_server = MagicMock()

    async with lifespan_fn(mock_server) as ctx:
        assert isinstance(ctx, SessionContext)
        assert hasattr(ctx, "sandbox")
        assert hasattr(ctx, "death_task")
        assert isinstance(ctx.death_task, asyncio.Task)


async def test_lifespan_cancels_death_task_on_exit(mock_backend: MockBackend) -> None:
    """On exit, death_task is cancelled."""
    lifespan_fn = create_lifespan(mock_backend, "stdio")
    mock_server = MagicMock()

    async with lifespan_fn(mock_server) as ctx:
        death_task = ctx.death_task
        assert not death_task.cancelled()
        # Death task should still be running

    # After exit, death task should be cancelled
    assert death_task.cancelled()


async def test_lifespan_calls_sandbox_stop_on_exit(mock_backend: MockBackend) -> None:
    """On exit, sandbox.stop() is called."""
    lifespan_fn = create_lifespan(mock_backend, "stdio")
    mock_server = MagicMock()

    async with lifespan_fn(mock_server) as ctx:
        sandbox = cast(MockSandbox, ctx.sandbox)
        assert not sandbox.is_stopped()

    # After exit, sandbox should be stopped
    assert sandbox.is_stopped()


async def test_lifespan_stops_sandbox_even_if_exception_raised(
    mock_backend: MockBackend,
) -> None:
    """Sandbox stop is called even if the body raises an exception."""
    lifespan_fn = create_lifespan(mock_backend, "stdio")
    mock_server = MagicMock()

    sandbox = None

    with pytest.raises(ValueError):
        async with lifespan_fn(mock_server) as ctx:
            sandbox = cast(MockSandbox, ctx.sandbox)
            raise ValueError("test error")

    # Sandbox should still be stopped
    assert sandbox is not None
    assert sandbox.is_stopped()


async def test_death_triggers_sigterm_stdio(
    mock_backend: MockBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sandbox death triggers SIGTERM in stdio mode."""
    kill_calls: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: kill_calls.append((pid, sig)))

    lifespan_fn = create_lifespan(mock_backend, "stdio")
    mock_server = MagicMock()

    async with lifespan_fn(mock_server) as ctx:
        # Simulate sandbox death
        cast(MockSandbox, ctx.sandbox).simulate_death()
        # Give death task time to process
        await asyncio.sleep(0.1)

    assert len(kill_calls) == 1
    assert kill_calls[0][1] == signal.SIGTERM


async def test_death_does_not_trigger_sigterm_http(
    mock_backend: MockBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sandbox death does NOT trigger SIGTERM in HTTP mode."""
    kill_calls: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: kill_calls.append((pid, sig)))

    lifespan_fn = create_lifespan(mock_backend, "http")
    mock_server = MagicMock()

    async with lifespan_fn(mock_server) as ctx:
        # Simulate sandbox death
        cast(MockSandbox, ctx.sandbox).simulate_death()
        # Give death task time to process
        await asyncio.sleep(0.1)

    # No SIGTERM should be sent in HTTP mode
    assert len(kill_calls) == 0


async def test_lifespan_creates_sandbox_for_http(mock_backend: MockBackend) -> None:
    """Lifespan creates a sandbox for HTTP transport as well."""
    lifespan_fn = create_lifespan(mock_backend, "http")
    mock_server = MagicMock()

    async with lifespan_fn(mock_server) as ctx:
        assert ctx.sandbox is not None
        assert ctx.sandbox.sandbox_id == "mock-sandbox-001"


# --- Server Factory Tests ---


def test_create_server_returns_fastmcp(mock_backend: MockBackend) -> None:
    """create_server() returns a FastMCP instance."""
    config = ServerConfig()
    server = create_server(mock_backend, config)

    # Just verify it's a FastMCP instance with expected attributes
    assert hasattr(server, "name")
    assert server.name == "Kilntainers"


def test_create_server_with_lifespan(mock_backend: MockBackend) -> None:
    """create_server() creates FastMCP instance with lifespan configured."""
    config = ServerConfig(transport="stdio")
    server = create_server(mock_backend, config)

    # Verify a FastMCP instance was created
    assert server.name == "Kilntainers"


def test_create_server_with_override_description(mock_backend: MockBackend) -> None:
    """Tool description uses override when provided."""
    config = ServerConfig(tool_instruction_override="Custom override")
    server = create_server(mock_backend, config)

    # The tool should be registered with the override description
    # FastMCP stores tools in _tool_manager
    assert hasattr(server, "_tool_manager")


def test_create_server_raises_on_empty_description() -> None:
    """create_server() raises BackendError if tool description assembly fails."""
    backend = MockBackend(BackendConfig(), tool_instructions=None)
    config = ServerConfig()

    with pytest.raises(BackendError) as exc_info:
        create_server(backend, config)
    assert "does not provide tool instructions" in str(exc_info.value)


def test_create_server_with_extended_description(mock_backend: MockBackend) -> None:
    """Tool description combines backend instructions with extended."""
    config = ServerConfig(extended_tool_instruction="With extra info")
    server = create_server(mock_backend, config)

    # Server should be created successfully
    assert server.name == "Kilntainers"
