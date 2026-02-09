"""Backend implementations and registry."""

from kilntainers.backends.docker import DockerBackend

# Maps --backend CLI values to backend classes
BACKEND_REGISTRY: dict[str, type] = {
    "docker": DockerBackend,
}


def get_backend_class(name: str) -> type:
    """Look up a backend class by name.

    Raises KeyError if the backend name is not registered.
    """
    if name not in BACKEND_REGISTRY:
        available = ", ".join(sorted(BACKEND_REGISTRY.keys()))
        raise KeyError(f"Unknown backend: '{name}'. Available backends: {available}")
    return BACKEND_REGISTRY[name]
