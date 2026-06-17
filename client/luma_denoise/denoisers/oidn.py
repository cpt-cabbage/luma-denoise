"""Intel Open Image Denoise (OIDN) backend."""

from __future__ import annotations

import os

from .base import ADDON_VERSION, DenoiserBackend, join_bin, quote


class OidnDenoiser(DenoiserBackend):
    """Builds the Deadline job that runs oidn_denoise.py on the farm.

    OIDN cannot read packed multi-channel render EXRs; the wrapper script
    extracts beauty/albedo/normal per frame via oiiotool, runs oidnDenoise,
    and reassembles the denoised frame. Tool paths are single values for the
    OIDN pool, resolved here at submit time.
    """

    name = "oidn"
    wrapper_filename = "oidn_denoise.py"
    requires_combine = True

    def get_arguments(self, instance, settings: dict) -> str:
        oidn_settings = self._backend_settings(settings)
        files = instance.data["files"]
        first_file = files[0]
        dirname = os.path.dirname(first_file).replace("\\", "/")
        basename = os.path.basename(first_file)
        frame_start = int(instance.data.get("frameStartHandle", 1))
        frame_end = int(instance.data.get("frameEndHandle", 1))

        oidn_exe = join_bin(
            oidn_settings.get("oidn_root_path", ""),
            oidn_settings.get("denoise_exe", "oidnDenoise") or "oidnDenoise")
        oiiotool = join_bin(
            oidn_settings.get("oiio_root_path", ""),
            oidn_settings.get("oiio_exe", "oiiotool") or "oiiotool")
        wrapper_path = self._resolve_wrapper_path(settings)

        parts = [quote(wrapper_path)]
        parts.extend(["--oidn-exe", quote(oidn_exe)])
        parts.extend(["--oiiotool", quote(oiiotool)])
        parts.extend([
            "--input", quote(f"{dirname}/{basename}"),
            "--output-dir", quote(f"{dirname}/denoised"),
            "--frame-start", str(frame_start),
            "--frame-end", str(frame_end),
            "--beauty-channel", quote(oidn_settings.get("beauty_channel", "beauty")),
            "--albedo-channel", quote(oidn_settings.get("albedo_channel", "albedo")),
            "--normal-channel", quote(oidn_settings.get("normal_channel", "N")),
            "--addon-version", ADDON_VERSION,
        ])
        parts.extend(self.rename_pair_args(settings))
        parts.append("--verbose")
        return " ".join(parts)

    def get_environment(self, settings: dict) -> dict:
        return {}

    def validate(self, instance, settings: dict) -> None:
        self._resolve_wrapper_path(settings)
        oidn_settings = self._backend_settings(settings)
        for field in ("oidn_root_path", "oiio_root_path"):
            if not oidn_settings.get(field, ""):
                raise RuntimeError(
                    f"luma-denoise: 'denoise.oidn.{field}' is not set. Point "
                    "it at the install root on the OIDN pool.")
        for field in ("beauty_channel", "albedo_channel", "normal_channel"):
            if not oidn_settings.get(field, ""):
                raise RuntimeError(
                    f"luma-denoise: 'oidn.{field}' is empty. OIDN requires "
                    "the beauty, albedo, and normal layer names to extract "
                    "them from the render EXR. Set them in the luma-denoise "
                    "project settings (OIDN group)."
                )
