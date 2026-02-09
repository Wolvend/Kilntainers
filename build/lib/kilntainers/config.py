"""Configuration dataclasses."""

from dataclasses import dataclass, field
from typing import Literal

Transport = Literal["stdio", "http"]


@dataclass(frozen=True, slots=True, kw_only=True)
class ServerConfig:
    """Core server configuration from CLI arguments.

    Consumed by the MCP server layer (Phase 4) and the startup
    orchestration logic. Does not contain backend-specific config.
    """

    # Transport
    transport: Transport = "stdio"
    host: str = "127.0.0.1"  # HTTP bind address
    port: int = 8435  # HTTP listen port

    # Exec defaults
    default_timeout: int = 120  # seconds
    output_limit: int = 2_097_152  # bytes (2 MiB)

    # Tool description
    tool_instruction_override: str | None = None
    extended_tool_instruction: str | None = None

    # Session management (HTTP only)
    session_timeout: int = 300  # seconds (5 minutes)


@dataclass(frozen=True, slots=True, kw_only=True)
class DockerBackendConfig:
    """Configuration for the Docker backend.

    Populated from CLI args by the startup layer. Consumed by
    DockerBackend (Phase 3).
    """

    engine: str = "docker"
    image: str = "debian:bookworm-slim"
    shell: str = "/bin/bash"
    network_enabled: bool = False
    cpu: str | None = None
    memory: str | None = None
    docker_run_flags: list[str] = field(default_factory=list)

    # Passed through for tool description generation
    default_timeout: int = 120
