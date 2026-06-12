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
        """
        return (settings.get("shared", {}) or {}).get(
            "python_executable", "python")

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
                f"luma-denoise: 'denoise.{self.name}.wrapper_script_path' is not "
                f"configured. Set it in the luma-denoise project settings to "
                f"the absolute path of {self.wrapper_filename or 'the wrapper script'} "
                "on a shared filesystem accessible from all render nodes. "
                "Use the {version} token for per-version paths, e.g. "
                "'L:/tools/.../luma_denoise_scripts/{version}/"
                f"{self.wrapper_filename}'."
            )
        return template.replace("{version}", ADDON_VERSION)

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
