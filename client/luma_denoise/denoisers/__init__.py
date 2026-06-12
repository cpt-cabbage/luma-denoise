"""Denoiser backend registry."""

from .base import DenoiserBackend
from .oidn import OidnDenoiser
from .renderman import RendermanDenoiser

_BACKENDS = {
    RendermanDenoiser.name: RendermanDenoiser,
    OidnDenoiser.name: OidnDenoiser,
}


def get_denoiser_backend(name: str) -> DenoiserBackend:
    """Return a backend instance for a settings 'denoiser' value.

    Raises:
        RuntimeError: for unknown names, listing the known backends.
    """
    backend_cls = _BACKENDS.get(name)
    if backend_cls is None:
        raise RuntimeError(
            f"luma-denoise: unknown denoiser '{name}'. "
            f"Known denoisers: {sorted(_BACKENDS)}."
        )
    return backend_cls()


__all__ = [
    "DenoiserBackend",
    "OidnDenoiser",
    "RendermanDenoiser",
    "get_denoiser_backend",
]
