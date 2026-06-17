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


def join_bin(root: str, exe: str) -> str:
    """Join an install root with bin/<exe>, used verbatim (no .exe magic)."""
    return f"{root.rstrip('/')}/bin/{exe}"


def resolve_wrapper_path(settings: dict, filename: str) -> str:
    """Join shared.scripts_directory with a wrapper filename.

    Reads the single scripts_directory setting (Deadline Path Mapping
    translates it per worker), substitutes {version}, strips a trailing
    slash, and appends the fixed wrapper filename. Raises with an
    actionable message when the directory is unset.
    """
    shared = settings.get("shared", {}) or {}
    directory = shared.get("scripts_directory", "")
    if not directory:
        raise RuntimeError(
            "luma-denoise: 'shared.scripts_directory' is not set. Point it "
            "at the folder containing the wrapper scripts on the shared "
            "library (Deadline Path Mapping translates it per worker). Use "
            "the {version} token for per-version paths."
        )
    directory = directory.replace("{version}", ADDON_VERSION).rstrip("/\\")
    return f"{directory}/{filename}"


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
        """Executable for the Deadline job — the backend's worker Python.

        Single value for the backend's single-OS pool (resolved at submit).
        """
        return self._backend_settings(settings).get(
            "python_executable", "python") or "python"

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
        return resolve_wrapper_path(settings, self.wrapper_filename)

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
