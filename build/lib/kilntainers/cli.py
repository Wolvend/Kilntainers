"""CLI argument parsing and main entry point."""

import argparse
import asyncio
import sys
from typing import NoReturn

from kilntainers.backends import get_backend_class
from kilntainers.config import DockerBackendConfig, ServerConfig
from kilntainers.errors import BackendError
from kilntainers.server import create_server

# Sentinel for detecting unset HTTP-only arguments
_UNSET = object()


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser.

    Returns:
        An ArgumentParser with all kilntainers arguments organized into groups.
    """
    parser = argparse.ArgumentParser(
        prog="kilntainers",
        description=(
            "MCP server providing isolated Linux sandboxes "
            "for LLM agent shell execution."
        ),
    )

    # --- Core parameters ---
    core = parser.add_argument_group("core options")
    core.add_argument(
        "--backend",
        default="docker",
        choices=["docker"],
        help="Backend to use (default: docker)",
    )
    core.add_argument(
        "--transport",
        default="stdio",
        choices=["stdio", "http"],
        help="MCP transport (default: stdio)",
    )
    core.add_argument(
        "--host",
        default=_UNSET,
        help="HTTP bind address (default: 127.0.0.1, HTTP mode only)",
    )
    core.add_argument(
        "--port",
        type=int,
        default=_UNSET,
        help="HTTP listen port (default: 8435, HTTP mode only)",
    )
    core.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="Default exec timeout in seconds (default: 120)",
    )
    core.add_argument(
        "--output-limit",
        type=int,
        default=2_097_152,
        help="Max combined stdout+stderr bytes per exec (default: 2097152 = 2 MiB)",
    )
    core.add_argument(
        "--session-timeout",
        type=int,
        default=_UNSET,
        help="Idle session timeout in seconds (default: 300, HTTP mode only)",
    )

    # --- Tool description ---
    desc = parser.add_argument_group("tool description")
    desc.add_argument(
        "--tool-instruction-override",
        default=None,
        help="Replace the entire shell_exec tool description",
    )
    desc.add_argument(
        "--extended-tool-instruction",
        default=None,
        help="Append to the backend's default tool description",
    )

    # --- Docker backend parameters ---
    docker = parser.add_argument_group("docker backend options")
    docker.add_argument(
        "--engine",
        default="docker",
        help="Container CLI binary (default: docker). Supports podman.",
    )
    docker.add_argument(
        "--image",
        default="debian:bookworm-slim",
        help="Docker image (default: debian:bookworm-slim)",
    )
    docker.add_argument(
        "--shell",
        default="/bin/bash",
        help="Shell binary for command mode (default: /bin/bash)",
    )
    docker.add_argument(
        "--network",
        action="store_true",
        default=False,
        help="Enable network access in sandboxes (default: disabled)",
    )
    docker.add_argument(
        "--cpu",
        default=None,
        help='Docker CPU limit (e.g., "1.5")',
    )
    docker.add_argument(
        "--memory",
        default=None,
        help='Docker memory limit (e.g., "512m")',
    )
    docker.add_argument(
        "--docker-run-flag",
        action="append",
        default=None,
        dest="docker_run_flags",
        help=(
            "Additional flag passed to docker run. Repeatable. "
            '(e.g., --docker-run-flag "--pids-limit=256")'
        ),
    )

    return parser


def build_configs(
    args: argparse.Namespace,
) -> tuple[ServerConfig, DockerBackendConfig]:
    """Build config dataclasses from parsed arguments.

    This function maps flat CLI arguments to the typed config objects
    consumed by the server and backend layers.

    Args:
        args: Parsed command-line arguments from argparse.

    Returns:
        A tuple of (ServerConfig, DockerBackendConfig).
    """
    # Handle HTTP-only args that may be _UNSET
    host = "127.0.0.1" if args.host is _UNSET else args.host
    port = 8435 if args.port is _UNSET else args.port
    session_timeout = 300 if args.session_timeout is _UNSET else args.session_timeout

    server_config = ServerConfig(
        transport=args.transport,
        host=host,
        port=port,
        default_timeout=args.timeout,
        output_limit=args.output_limit,
        tool_instruction_override=args.tool_instruction_override,
        extended_tool_instruction=args.extended_tool_instruction,
        session_timeout=session_timeout,
    )

    docker_config = DockerBackendConfig(
        engine=args.engine,
        image=args.image,
        shell=args.shell,
        network_enabled=args.network,
        cpu=args.cpu,
        memory=args.memory,
        docker_run_flags=args.docker_run_flags or [],
        default_timeout=args.timeout,
    )

    return server_config, docker_config


def _startup_error(message: str) -> NoReturn:
    """Print an error message to stderr and exit with code 1.

    Used for all startup/configuration errors. Follows D31 (no logging —
    stderr for error reporting).

    Args:
        message: The error message to display.

    Raises:
        SystemExit: Always exits with code 1.
    """
    print(f"kilntainers: error: {message}", file=sys.stderr)
    sys.exit(1)


def validate_config(
    server_config: ServerConfig,
    docker_config: DockerBackendConfig,
    _parsed_args: argparse.Namespace | None = None,
) -> None:
    """Validate configuration constraints that span multiple parameters.

    Raises SystemExit with a descriptive message on failure.
    Individual argument type validation is handled by argparse.
    Cross-cutting constraints are checked here.

    Args:
        server_config: The server configuration to validate.
        docker_config: The Docker backend configuration (unused in v1 validation,
            but accepted for interface consistency).
        _parsed_args: The parsed arguments for detecting HTTP-only args.
            Internal parameter for testing - normally None, which means
            the function uses the server_config values to detect explicit
            HTTP-only argument setting.

    Raises:
        SystemExit: If validation fails, with code 1 and an error message.
    """
    # HTTP-only parameters in stdio mode
    # When called from main(), _parsed_args contains the actual parsed args
    # When called from tests, we rely on comparing config values to defaults
    if server_config.transport == "stdio":
        # Check if user explicitly passed HTTP-only args
        # We check if values differ from defaults, indicating explicit setting
        if server_config.host != "127.0.0.1":
            _startup_error(
                "--host is only valid with --transport http. "
                "In stdio mode, there is no HTTP server to bind."
            )
        if server_config.port != 8435:
            _startup_error(
                "--port is only valid with --transport http. "
                "In stdio mode, there is no HTTP server to bind."
            )
        if server_config.session_timeout != 300:
            _startup_error(
                "--session-timeout is only valid with --transport http. "
                "In stdio mode, the session lives as long as the process."
            )

    # Mutual exclusivity: tool description params
    if (
        server_config.tool_instruction_override is not None
        and server_config.extended_tool_instruction is not None
    ):
        _startup_error(
            "Cannot use both --tool-instruction-override and "
            "--extended-tool-instruction. Use override to replace "
            "the description entirely, or extended to append to "
            "the backend default."
        )

    # Timeout must be positive
    if server_config.default_timeout < 1:
        _startup_error("--timeout must be at least 1 second.")

    # Output limit must be positive
    if server_config.output_limit < 1:
        _startup_error("--output-limit must be at least 1 byte.")


async def _async_main(
    server_config: ServerConfig,
    docker_config: DockerBackendConfig,
) -> None:
    """Async startup: validate backend, build server, run.

    This function performs all async startup operations:
    - Creates and validates the backend
    - Creates the MCP server
    - Runs the transport (blocking until shutdown)

    Args:
        server_config: Server configuration.
        docker_config: Docker backend configuration.

    Raises:
        SystemExit: If backend validation or server creation fails.
    """
    # Create and validate backend
    backend_class = get_backend_class("docker")  # Only docker in v1
    backend = backend_class(docker_config)
    try:
        await backend.validate()
    except BackendError as e:
        _startup_error(str(e))

    # Create the MCP server (assembles tool description, registers tool)
    try:
        mcp = create_server(backend, server_config)
    except BackendError as e:
        _startup_error(str(e))

    # Run the transport (blocks until shutdown)
    transport = "stdio" if server_config.transport == "stdio" else "streamable-http"
    mcp.run(transport=transport)


def main() -> None:
    """CLI entry point. Parses args, configures, and runs the server.

    This is the main entry point for the kilntainers command. It:
    1. Parses CLI arguments
    2. Builds configuration objects
    3. Validates configuration constraints
    4. Creates and validates the backend
    5. Creates and runs the MCP server

    Never returns normally (exits on KeyboardInterrupt or server shutdown).
    """
    parser = build_parser()
    args = parser.parse_args()

    server_config, docker_config = build_configs(args)
    validate_config(server_config, docker_config)

    # Run the async startup + server
    try:
        asyncio.run(_async_main(server_config, docker_config))
    except KeyboardInterrupt:
        pass  # Clean exit on Ctrl+C
