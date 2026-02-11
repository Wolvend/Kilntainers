"""MCP server implementation."""

import asyncio
import json
import os
import signal
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Any, AsyncContextManager

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession
from mcp.types import CallToolResult, TextContent
from pydantic import Field

from kilntainers.backends.base import Backend, ExecRequest, Sandbox
from kilntainers.config import ServerConfig
from kilntainers.errors import BackendError, SandboxDiedError

# Constants
STDIN_LIMIT = 2 * 1024 * 1024  # 2 MiB (D32)


# --- Session Context ---


@dataclass
class SessionContext:
    """Per-session state, available to tool handlers via Context."""

    sandbox: Sandbox
    death_task: asyncio.Task[None]


# --- Tool Description Assembly ---


def assemble_tool_description(
    backend: Backend,
    override: str | None,
    extended: str | None,
) -> str:
    """Assemble the sandbox_exec tool description.

    Raises BackendError if the result would be empty.

    Args:
        backend: The backend instance to query for tool instructions.
        override: User-provided description that replaces everything.
        extended: User-provided text to append to backend instructions.

    Returns:
        The assembled tool description text.

    Raises:
        BackendError: If both override and extended are provided, or if
            the result would be empty.
    """
    # Rule 4: Both override and extended is an error
    if override is not None and extended is not None:
        raise BackendError(
            "Cannot use both --tool-instruction-override and "
            "--extended-tool-instruction. Use override to replace "
            "the description entirely, or extended to append to "
            "the backend default."
        )

    # Rule 1: Override replaces everything
    if override is not None:
        return override

    # Rule 2: Backend instructions, optionally extended
    backend_instructions = backend.tool_instructions()

    if not backend_instructions:
        # Rule 3: No backend instructions and no override
        raise BackendError(
            "Backend does not provide tool instructions describing "
            "the sandbox. Supply --tool-instruction-override to "
            "describe the capabilities of this sandbox (example "
            "'a Debian Linux bash shell' or 'A minimal BusyBox "
            "shell with the following commands: ...')."
        )

    if extended is not None:
        return f"{backend_instructions}\n\n{extended}"

    return backend_instructions


# --- Lifespan Factory ---


def create_lifespan(
    backend: Backend,
    transport: str,
    *,
    death_callback: Callable[[], None] | None = None,
) -> Callable[[FastMCP], AsyncContextManager[SessionContext]]:
    """Create a lifespan context manager for the given transport.

    The returned context manager creates a sandbox per session and
    handles death propagation appropriate to the transport.

    Args:
        backend: The backend to use for creating sandboxes.
        transport: The transport mode ("stdio" or "http").
        death_callback: Optional callback for sandbox death in stdio mode.
            If None, sends SIGTERM to current process. For testing, pass
            a custom callback to capture death notifications.

    Returns:
        An async context manager function compatible with FastMCP.
    """

    @asynccontextmanager
    async def lifespan(server: FastMCP) -> AsyncIterator[SessionContext]:
        """Create a sandbox for this session and clean up on exit."""
        sandbox = await backend.create_sandbox()

        async def _monitor_death() -> None:
            """Monitor sandbox death and trigger appropriate shutdown."""
            try:
                await sandbox.wait_for_death()
            except asyncio.CancelledError:
                # Normal shutdown — propagate cancellation
                raise
            except Exception:
                # Unexpected error monitoring sandbox — treat as death
                # We don't have logging (D31), so we just proceed with death handling
                pass

            # If we reach here, sandbox died (or monitoring failed)
            if transport == "stdio":
                if death_callback is not None:
                    # Test mode: call custom callback instead of sending signal
                    death_callback()
                else:
                    # Trigger process shutdown via SIGTERM to self
                    # This reuses the existing graceful shutdown path
                    os.kill(os.getpid(), signal.SIGTERM)
            # For HTTP: sandbox is dead; subsequent exec calls will raise
            # SandboxDiedError. Proactive session termination can be
            # added here if SDK supports it.

        death_task = asyncio.create_task(_monitor_death())

        try:
            yield SessionContext(sandbox=sandbox, death_task=death_task)
        finally:
            death_task.cancel()
            try:
                await death_task
            except asyncio.CancelledError:
                pass
            await sandbox.stop()

    return lifespan


# --- Input Validation ---


def _validate_inputs(
    command: str | None,
    args: list[str] | None,
    stdin: str | None,
    working_directory: str | None,
    timeout: int | None,
) -> str | None:
    """Validate tool inputs.

    Returns error message or None if valid.

    Args:
        command: The shell command string, if using command mode.
        args: The list of arguments, if using args mode.
        stdin: The stdin content to pipe to the command.
        working_directory: The working directory for the command.
        timeout: The timeout in seconds.

    Returns:
        An error message string if validation fails, None otherwise.
    """
    # Exactly one of command or args
    if command is not None and args is not None:
        return "Cannot provide both 'command' and 'args'. Use 'command' for shell commands or 'args' for direct execution."
    if command is None and args is None:
        return "Must provide either 'command' or 'args'."

    # working_directory must be absolute
    if working_directory is not None and not working_directory.startswith("/"):
        return f"working_directory must be an absolute path, got: {working_directory}"

    # timeout must be positive
    if timeout is not None and timeout < 1:
        return "timeout must be at least 1 second."

    # stdin size limit (D32)
    if stdin is not None and len(stdin.encode("utf-8")) > STDIN_LIMIT:
        return (
            f"stdin content exceeds the 2 MiB limit "
            f"({len(stdin.encode('utf-8'))} bytes). "
            f"Split into smaller chunks or use a different approach."
        )

    return None


# --- Tool Handler ---


def _create_handler(config: ServerConfig) -> Callable[..., Any]:
    """Create the sandbox_exec handler with server config bound via closure.

    Args:
        config: The server configuration containing defaults.

    Returns:
        An async handler function for the sandbox_exec tool.
    """

    async def sandbox_exec_handler(
        command: str | None = None,
        args: list[str] | None = None,
        stdin: str | None = None,
        working_directory: str | None = None,
        timeout: int | None = None,
        ctx: Context[ServerSession, SessionContext] | None = None,
    ) -> CallToolResult:
        """Handle a sandbox_exec tool call.

        Args:
            command: Shell command string (mutually exclusive with args).
            args: List of arguments for direct execution (mutually exclusive with command).
            stdin: Content to pipe to stdin.
            working_directory: Working directory for the command (must be absolute).
            timeout: Timeout in seconds (defaults to server config).
            ctx: FastMCP context object (injected automatically).

        Returns:
            A CallToolResult with the execution result or error.
        """
        # --- Input validation ---
        error = _validate_inputs(command, args, stdin, working_directory, timeout)
        if error is not None:
            return CallToolResult(
                content=[TextContent(type="text", text=error)],
                isError=True,
            )

        # --- Get sandbox from context ---
        # ctx should always be provided by FastMCP, but handle None for safety
        if ctx is None:
            return CallToolResult(
                content=[
                    TextContent(type="text", text="Internal error: no context provided")
                ],
                isError=True,
            )

        sandbox = ctx.request_context.lifespan_context.sandbox

        # --- Construct ExecRequest ---
        request = ExecRequest(
            command=command,
            args=args,
            stdin=stdin,
            working_directory=working_directory,
            timeout=timeout if timeout is not None else config.default_timeout,
            output_limit=config.output_limit,
        )

        # --- Execute ---
        try:
            result = await sandbox.exec(request)
        except SandboxDiedError as e:
            return CallToolResult(
                content=[TextContent(type="text", text=str(e))],
                isError=True,
            )

        # --- Format response ---
        response_json = json.dumps(
            {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.exit_code,
                "exec_duration_ms": result.exec_duration_ms,
            }
        )

        return CallToolResult(
            content=[TextContent(type="text", text=response_json)],
            isError=False,
        )

    return sandbox_exec_handler


# --- Server Factory ---


def create_server(
    backend: Backend,
    config: ServerConfig,
) -> FastMCP:
    """Create and configure the MCP server.

    Args:
        backend: Validated backend instance.
        config: Server configuration (transport, host, port, timeouts, etc.).

    Returns:
        Configured FastMCP instance ready to run.

    Raises:
        BackendError: If tool description assembly fails.
    """
    # Assemble tool description
    description = assemble_tool_description(
        backend,
        override=config.tool_instruction_override,
        extended=config.extended_tool_instruction,
    )

    # Create lifespan that captures the backend and transport
    lifespan = create_lifespan(backend, config.transport)

    # Create server
    mcp = FastMCP(
        name="Kilntainers",
        lifespan=lifespan,
        host=config.host,
        port=config.port,
    )

    handler = _create_handler(config)

    # Wrapper closure for better MCP type hinting
    # type ignore and noqa needed to get the right type hints. Type hinting doesn't work for Optional[str] so str but assign None as default.
    async def sandbox_exec(
        command: Annotated[
            str,  # noqa: RUF013
            Field(description="Shell command string (mutually exclusive with args)."),
        ] = None,  # type: ignore
        args: Annotated[
            list[str],  # noqa: RUF013
            Field(
                description="List of arguments for direct execution (mutually exclusive with command)."
            ),
        ] = None,  # type: ignore
        stdin: Annotated[str, Field(description="Content to pipe to stdin.")] = None,  # type: ignore # noqa: RUF013
        working_directory: Annotated[
            str,  # noqa: RUF013
            Field(description="Working directory for the command (must be absolute)."),
        ] = None,  # type: ignore
        timeout: Annotated[
            int,  # noqa: RUF013
            Field(description="Timeout in seconds (defaults to server config)."),
        ] = None,  # type: ignore
        ctx: Context[ServerSession, SessionContext] | None = None,
    ) -> CallToolResult:
        return await handler(
            command=command,
            args=args,
            stdin=stdin,
            working_directory=working_directory,
            timeout=timeout,
            ctx=ctx,
        )

    mcp.add_tool(
        sandbox_exec,
        name="sandbox_exec",
        description=description,
    )

    return mcp
