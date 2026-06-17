"""Pixar RenderMan denoise_batch backend."""

from __future__ import annotations

import os

from .base import DenoiserBackend, join_bin, quote


class RendermanDenoiser(DenoiserBackend):
    """Builds the Deadline job that runs Pixar denoise_batch directly.

    RenderMan's denoiser is a native executable, so the job launches it
    directly (no Python wrapper). The combine step gets its beauty rename
    map from this backend's beauty_rename_map setting.
    """

    name = "renderman"
    wrapper_filename = ""
    requires_combine = True

    def get_executable(self, settings: dict) -> str:
        rm = self._backend_settings(settings)
        root = rm.get("rmantree_path", "")
        exe = rm.get("denoise_exe", "denoise_batch") or "denoise_batch"
        return join_bin(root, exe)

    def get_arguments(self, instance, settings: dict) -> str:
        rm_settings = self._backend_settings(settings)
        files = instance.data["files"]
        first_file = files[0]
        dirname = os.path.dirname(first_file).replace("\\", "/")
        basename = os.path.basename(first_file)
        frame_start = int(instance.data.get("frameStartHandle", 1))
        frame_end = int(instance.data.get("frameEndHandle", 1))

        parts = ["-a", "0", "-v", "--clean-alpha", "--progress"]
        if self._frame_count(instance) >= 8:
            parts.append("-cf")
        if self.detect_large_image(instance, rm_settings):
            parts.extend(["--tiles", "2", "2"])
        parts.extend([
            "-o", quote(f"{dirname}/denoised"),
            quote(f"{dirname}/{basename}"),
            f"{frame_start}-{frame_end}",
        ])
        return " ".join(parts)

    def get_environment(self, settings: dict) -> dict:
        rm = self._backend_settings(settings)
        env = {"RMANTREE": rm.get("rmantree_path", "")}
        license_value = rm.get("pixar_license", "")
        if license_value:
            env["PIXAR_LICENSE_FILE"] = license_value
        return env

    def validate(self, instance, settings: dict) -> None:
        rm = self._backend_settings(settings)
        if not rm.get("rmantree_path", ""):
            raise RuntimeError(
                "luma-denoise: 'denoise.renderman.rmantree_path' is not set. "
                "Point it at the RenderManProServer install on the denoise "
                "pool so denoise_batch can be launched.")

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
