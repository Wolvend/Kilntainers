"""Backend implementations and registry."""

from kilntainers.backends.base import Backend
from kilntainers.backends.docker import DockerBackend
from kilntainers.backends.modal import ModalBackend

# Maps --backend CLI values to backend classes
BACKEND_REGISTRY: dict[str, type[Backend]] = {
    "docker": DockerBackend,
    "modal": ModalBackend,
}


def get_backend_class(name: str) -> type[Backend]:
    """Look up a backend class by name.

    Raises KeyError if the backend name is not registered.

    Args:
        name: The backend name from the --backend CLI argument.

    Returns:
        The Backend subclass for the given name.

    Raises:
        KeyError: If the backend name is not in the registry.
    """
    if name not in BACKEND_REGISTRY:
        available = ", ".join(sorted(BACKEND_REGISTRY.keys()))
        raise KeyError(f"Unknown backend: '{name}'. Available backends: {available}")
    return BACKEND_REGISTRY[name]
