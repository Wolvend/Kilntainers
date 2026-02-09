"""Docker backend implementation."""

from kilntainers.backends.base import Backend, Sandbox
from kilntainers.config import DockerBackendConfig


class DockerBackend(Backend):
    """Docker backend implementation.

    This is a stub for Phase 2. Phase 3 will implement the full
    Docker sandbox lifecycle and command execution engine.
    """

    def __init__(self, config: DockerBackendConfig) -> None:
        super().__init__()
        self._config = config

    async def _validate(self) -> None:
        """Validate Docker prerequisites (stub)."""
        raise NotImplementedError("DockerBackend will be implemented in Phase 3")

    async def _create_sandbox(self) -> Sandbox:
        """Create a Docker sandbox (stub)."""
        raise NotImplementedError("DockerBackend will be implemented in Phase 3")

    def tool_instructions(self) -> str | None:
        """Return tool description for Docker backend (stub)."""
        # Return a basic description for now
        return (
            f"A {self._config.image} Linux sandbox with {self._config.shell} shell. "
            f"Commands run with a {self._config.default_timeout}s timeout and "
            f"{self._config.output_limit if hasattr(self._config, 'output_limit') else '2MiB'} "
            f"output limit."
        )


class DockerSandbox(Sandbox):
    """Docker sandbox implementation (stub)."""

    def __init__(self) -> None:
        self._stopped = False

    @property
    def sandbox_id(self) -> str:
        raise NotImplementedError("DockerSandbox will be implemented in Phase 3")

    async def exec(self, request):
        raise NotImplementedError("DockerSandbox will be implemented in Phase 3")

    async def stop(self) -> None:
        self._stopped = True

    async def wait_for_death(self) -> None:
        raise NotImplementedError("DockerSandbox will be implemented in Phase 3")
