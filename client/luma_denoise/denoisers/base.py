"""Denoiser backend abstraction for the luma-denoise Deadline pipeline.

A backend is a pure strategy object: given the pyblish instance and the
luma-denoise settings dict it computes the executable, CLI arguments, and
environment for the Deadline CommandLine denoise job. All Deadline submission
mechanics stay in the Pyblish plugin (luma_denoise_publish.py).
"""

from __future__ import annotations

try:
    from luma_denoise.version import __version__ as ADDON_VERSION
except Exception:
    ADDON_VERSION = "unknown"


def quote(value: str) -> str:
    """Wrap a value in double quotes if it contains spaces."""
    value = str(value)
    if " " in value and not (value.startswith('"') and value.endswith('"')):
        return f'"{value}"'
    return value


def resolve_platform_value(value, worker_platform: str) -> str:
    """Resolve a multiplatform settings value for a worker platform.

    Accepts the {windows, linux, darwin} dict shape (AYON multiplatform
    path); plain strings pass through so pre-0.4.0 values keep working.
    """
    if isinstance(value, dict):
        return value.get(worker_platform, "") or ""
    return value or ""


class DenoiserBackend:
    """Base class for denoiser backends.

    Subclasses set ``name`` / ``wrapper_filename`` and implement
    ``get_arguments``, ``get_environment``, and ``validate``.
    """

    #: Settings key and registry name ("renderman", "oidn", ...)
    name = ""
    #: Wrapper script filename, used in error messages.
    wrapper_filename = ""
    #: Whether the OIIO combine job is required after this denoiser.
    requires_combine = True

    def get_executable(self, settings: dict) -> str:
        """Executable for the Deadline job — the worker Python.

        Both current backends run Python wrapper scripts, so this is the
        same ``shared.python_executable`` setting the combine wrapper uses.
        Single value — Deadline Path Mapping translates it per worker OS.
        """
        shared = settings.get("shared", {}) or {}
        return shared.get("python_executable", "python") or "python"

    def get_arguments(self, instance, settings: dict) -> str:
        """Full Arguments string for the Deadline CommandLine plugin."""
        raise NotImplementedError

    def get_environment(self, settings: dict) -> dict:
        """Environment variables to set on the Deadline job."""
        raise NotImplementedError

    def validate(self, instance, settings: dict) -> None:
        """Raise RuntimeError with an actionable message on bad config."""
        raise NotImplementedError

    # -- shared helpers -------------------------------------------------

    def _backend_settings(self, settings: dict) -> dict:
        denoise_settings = settings.get("denoise", {}) or {}
        return denoise_settings.get(self.name, {}) or {}

    def _resolve_wrapper_path(self, settings: dict) -> str:
        template = self._backend_settings(settings).get("wrapper_script_path", "")
        if not template:
            raise RuntimeError(
                f"luma-denoise: 'denoise.{self.name}.wrapper_script_path' is "
                "not set. Point it at "
                f"{self.wrapper_filename or 'the wrapper script'} on the shared "
                "library (Deadline Path Mapping translates it per worker). "
                "Use the {version} token for per-version paths."
            )
        return template.replace("{version}", ADDON_VERSION)

    @staticmethod
    def platform_triplet_args(prefix: str, value) -> list:
        """Emit ['--<prefix>-<plat>', v, ...] for each NON-EMPTY per-OS value
        from a {windows,linux,darwin} dict (or a plain string applied to all
        three). Empty values are OMITTED entirely — the wrapper's argparse
        defaults them to "" — which avoids emitting empty command-line tokens
        (a literal '""' could be read back as a truthy 2-char string and
        mask a 'no root for this platform' error)."""
        if isinstance(value, dict):
            vals = {p: (value.get(p, "") or "") for p in
                    ("windows", "linux", "darwin")}
        else:
            v = value or ""
            vals = {"windows": v, "linux": v, "darwin": v}
        out = []
        for p in ("windows", "linux", "darwin"):
            v = vals[p]
            if v:
                out.extend([f"--{prefix}-{p}", quote(v)])
        return out

    def rename_pair_args(self, settings: dict) -> list:
        """Backend's beauty_rename_map as ['--rename', 'SRC=DST', ...].

        The wrapper records these pairs verbatim in the denoise manifest's
        beauty_channel_map; the combine step consumes them from there.
        """
        args = []
        pairs = self._backend_settings(settings).get(
            "beauty_rename_map", []) or []
        for pair in pairs:
            if isinstance(pair, dict):
                source = pair.get("source", "")
                target = pair.get("target", "")
            else:
                source = getattr(pair, "source", "")
                target = getattr(pair, "target", "")
            if source and target:
                args.extend(["--rename", quote(f"{source}={target}")])
        return args
