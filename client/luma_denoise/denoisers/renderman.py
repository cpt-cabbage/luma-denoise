"""Pixar RenderMan denoise_batch backend."""

from __future__ import annotations

import os

from .base import ADDON_VERSION, DenoiserBackend, quote


class RendermanDenoiser(DenoiserBackend):
    """Builds the Deadline job that runs renderman_denoise.py on the farm."""

    name = "renderman"
    wrapper_filename = "renderman_denoise.py"
    requires_combine = True

    def get_arguments(self, instance, settings: dict) -> str:
        rm_settings = self._backend_settings(settings)
        files = instance.data["files"]
        first_file = files[0]
        dirname = os.path.dirname(first_file).replace("\\", "/")
        basename = os.path.basename(first_file)
        frame_start = int(instance.data.get("frameStartHandle", 1))
        frame_end = int(instance.data.get("frameEndHandle", 1))

        rman_root = rm_settings.get(
            "rmantree_path", "/opt/pixar/RenderManProServer-26.3")
        exe_name = rm_settings.get("denoise_exe", "denoise_batch")
        denoise_exe = f"{rman_root}/bin/{exe_name}"

        wrapper_path = self._resolve_wrapper_path(settings)

        parts = [
            quote(wrapper_path),
            "--denoise-exe", quote(denoise_exe),
            "--input", quote(f"{dirname}/{basename}"),
            "--output-dir", quote(f"{dirname}/denoised"),
            "--frame-start", str(frame_start),
            "--frame-end", str(frame_end),
            "--addon-version", ADDON_VERSION,
        ]
        if self._frame_count(instance) >= 8:
            parts.append("--cross-frame")
        if self.detect_large_image(instance, rm_settings):
            parts.extend(["--tiles", "2", "2"])
        parts.extend(self.rename_pair_args(settings))
        parts.append("--verbose")
        return " ".join(parts)

    def get_environment(self, settings: dict) -> dict:
        rm_settings = self._backend_settings(settings)
        env = {}
        rman_root = rm_settings.get("rmantree_path", "")
        if rman_root:
            env["RMANTREE"] = rman_root
            env["PATH"] = f"{rman_root}/bin"
        license_server = rm_settings.get("pixar_license", "")
        if license_server:
            env["PIXAR_LICENSE_FILE"] = license_server
        return env

    def validate(self, instance, settings: dict) -> None:
        # Raises with an actionable message when unset.
        self._resolve_wrapper_path(settings)

    # -- frame counting --------------------------------------------------

    def _frame_count(self, instance) -> int:
        frame_start = int(instance.data.get("frameStartHandle", 1))
        frame_end = int(instance.data.get("frameEndHandle", 1))
        length = frame_end - frame_start + 1

        publish_attrs = instance.data.get("publish_attributes", {})
        jobinfo_attrs = publish_attrs.get("CollectJobInfo", {})
        use_custom = jobinfo_attrs.get("use_custom_frames", "none")
        if use_custom in ("custom_only", "reuse_last_version"):
            custom_frames_str = jobinfo_attrs.get("frames", "")
            if custom_frames_str:
                length = self._count_custom_frames(custom_frames_str)
        return length

    @staticmethod
    def _count_custom_frames(frames_str: str) -> int:
        """Count individual frames from a custom frames string.

        Supports formats like "1001,1003-1006,1010" and returns the
        total number of frames represented.
        """
        count = 0
        for part in frames_str.replace(" ", "").split(","):
            if "-" in part:
                tokens = part.split("-", 1)
                try:
                    count += int(tokens[1]) - int(tokens[0]) + 1
                except (ValueError, IndexError):
                    count += 1
            elif part:
                count += 1
        return max(count, 1)

    # -- USD resolution inspection (Houdini-only, imports at call time) --

    def detect_large_image(self, instance, rm_settings: dict) -> bool:
        """True when any render product resolution meets the tiled threshold."""
        import hou
        from pxr import Usd, UsdRender
        from ayon_houdini.api.usd import (
            get_usd_rop_loppath,
            get_usd_render_rop_rendersettings,
        )

        threshold = int(rm_settings.get("tiled_denoise_threshold", 2048))

        rop_node = hou.node(instance.data["instance_node"])
        lop_node = get_usd_rop_loppath(rop_node)
        if not lop_node:
            return False

        stage = lop_node.stage()
        render_settings = get_usd_render_rop_rendersettings(rop_node, stage)
        if not render_settings:
            return False

        sample_time = Usd.TimeCode.EarliestTime()
        resolution_attributes = [render_settings.GetResolutionAttr()]
        for product in self._iter_render_products(render_settings, stage):
            resolution_attr = product.GetResolutionAttr()
            if resolution_attr.HasAuthoredValue():
                resolution_attributes.append(resolution_attr)

        for res_attr in resolution_attributes:
            resolution = res_attr.Get(sample_time)
            if resolution is None:
                continue
            if resolution[0] >= threshold or resolution[1] >= threshold:
                return True
        return False

    @staticmethod
    def _iter_render_products(render_settings, stage):
        from pxr import UsdRender

        for product_path in render_settings.GetProductsRel().GetTargets():
            prim = stage.GetPrimAtPath(product_path)
            if not prim.IsValid():
                return
            if prim.IsA(UsdRender.Product):
                yield UsdRender.Product(prim)
