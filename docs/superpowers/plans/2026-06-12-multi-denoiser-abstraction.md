# Multi-Denoiser Abstraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Abstract the denoise step behind `DenoiserBackend` strategy classes so the pipeline supports both Pixar RenderMan (`denoise_batch`) and Intel OIDN (`oidnDenoise`), selected by a project-settings dropdown, with both backends honoring a sidecar-manifest contract consumed by the OIIO combine step.

**Architecture:** A new pure-Python `client/luma_denoise/denoisers/` package computes executable/arguments/environment for the Deadline CommandLine job; the existing `LumaDenoiseUsdRender` Pyblish plugin keeps all Deadline mechanics and delegates backend specifics. Two new farm wrapper scripts (`renderman_denoise.py`, `oidn_denoise.py`) deploy like the existing `oiio_combine.py` and write a `<seq>.denoise.json` manifest; `oiio_combine.py` learns to read it.

**Tech Stack:** Python 3.9 (client/scripts use `from __future__ import annotations`), pytest, AYON addon (Pydantic server settings), Deadline CommandLine plugin.

**Spec:** `docs/superpowers/specs/2026-06-12-denoiser-abstraction-design.md`

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `client/luma_denoise/denoisers/__init__.py` | Backend registry (`get_denoiser_backend`) — empty until Task 4 |
| Create | `client/luma_denoise/denoisers/base.py` | `DenoiserBackend` base class, `quote()`, addon-version lookup |
| Create | `client/luma_denoise/denoisers/renderman.py` | RenderMan backend: args (cross-frame/tiles), env, USD resolution helpers |
| Create | `client/luma_denoise/denoisers/oidn.py` | OIDN backend: args, env |
| Create | `client/luma_denoise/scripts/renderman_denoise.py` | Farm wrapper: run `denoise_batch`, write manifest |
| Create | `client/luma_denoise/scripts/oidn_denoise.py` | Farm wrapper: extract channels, run `oidnDenoise` per frame, write manifest |
| Modify | `client/luma_denoise/scripts/oiio_combine.py` | Read `*.denoise.json`, manifest rename map overrides settings map |
| Modify | `client/luma_denoise/plugins/publish/houdini/luma_denoise_publish.py` | Delegate to backend; remove RenderMan-specific code |
| Modify | `server/settings.py` | `denoiser` enum + nested `renderman`/`oidn` groups; remove old flat fields |
| Modify | `package.py` | Version `0.1.4` → `0.2.0` |
| Modify | `CLAUDE.md` | Document new architecture; fix stale "no test suite" line |
| Create | `tests/test_denoiser_backends.py` | Backend unit tests |
| Create | `tests/test_renderman_denoise.py` | RenderMan wrapper unit tests |
| Create | `tests/test_oidn_denoise.py` | OIDN wrapper unit tests |
| Modify | `tests/test_oiio_combine.py` | Tests for denoise-manifest reading |

**Import pattern for tests** (the `luma_denoise/__init__.py` package imports AYON modules unavailable locally, so tests must NOT import `luma_denoise.*`): tests insert `client/luma_denoise` on `sys.path` and import `denoisers` as a top-level package. All intra-package imports in `denoisers/` are therefore **relative** (`from .base import ...`). The production plugin imports it as `luma_denoise.denoisers` — both paths work.

**Run all tests with:** `python -m pytest tests -v` (from repo root `C:\Users\christophe.leyder\_ayon_manager\luma-denoise`).

---

### Task 1: `DenoiserBackend` base class

**Files:**
- Create: `client/luma_denoise/denoisers/__init__.py` (empty for now)
- Create: `client/luma_denoise/denoisers/base.py`
- Create: `tests/test_denoiser_backends.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_denoiser_backends.py`:

```python
"""Unit tests for the luma_denoise.denoisers backend package."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# Import 'denoisers' as a top-level package; luma_denoise/__init__.py pulls in
# AYON modules that are not installed locally, so we must not go through it.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client" / "luma_denoise"))

from denoisers import base  # noqa: E402


def make_instance(**data):
    """Minimal stand-in for a pyblish instance (only .data is used)."""
    defaults = {
        "files": ["/renders/shot/main/shot_main.1001.exr"],
        "frameStartHandle": 1001,
        "frameEndHandle": 1100,
    }
    defaults.update(data)
    return SimpleNamespace(data=defaults)


def test_quote_adds_quotes_only_when_needed():
    assert base.quote("/plain/path.py") == "/plain/path.py"
    assert base.quote("/has space/p.py") == '"/has space/p.py"'
    assert base.quote('"/already quoted/p.py"') == '"/already quoted/p.py"'


def test_base_get_executable_uses_python_executable_setting():
    backend = base.DenoiserBackend()
    assert backend.get_executable({"python_executable": "/py/bin/python3"}) == "/py/bin/python3"
    assert backend.get_executable({}) == "python"


def test_base_resolve_wrapper_path_substitutes_version():
    backend = base.DenoiserBackend()
    backend.name = "renderman"
    settings = {"renderman": {
        "wrapper_script_path": "L:/scripts/{version}/renderman_denoise.py"}}
    resolved = backend._resolve_wrapper_path(settings)
    assert "{version}" not in resolved
    assert resolved.endswith("/renderman_denoise.py")


def test_base_resolve_wrapper_path_empty_raises_actionable_error():
    backend = base.DenoiserBackend()
    backend.name = "renderman"
    with pytest.raises(RuntimeError, match="wrapper_script_path"):
        backend._resolve_wrapper_path({"renderman": {"wrapper_script_path": ""}})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_denoiser_backends.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'denoisers'`

- [ ] **Step 3: Write the implementation**

Create `client/luma_denoise/denoisers/__init__.py` as an **empty file** (registry comes in Task 4).

Create `client/luma_denoise/denoisers/base.py`:

```python
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
        same ``python_executable`` setting the combine wrapper already uses.
        """
        return settings.get("python_executable", "python")

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
        return settings.get(self.name, {}) or {}

    def _resolve_wrapper_path(self, settings: dict) -> str:
        template = self._backend_settings(settings).get("wrapper_script_path", "")
        if not template:
            raise RuntimeError(
                f"luma-denoise: '{self.name}.wrapper_script_path' is not "
                f"configured. Set it in the luma-denoise project settings to "
                f"the absolute path of {self.wrapper_filename or 'the wrapper script'} "
                "on a shared filesystem accessible from all render nodes. "
                "Use the {version} token for per-version paths, e.g. "
                "'L:/tools/.../luma_denoise_scripts/{version}/"
                f"{self.wrapper_filename}'."
            )
        return template.replace("{version}", ADDON_VERSION)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_denoiser_backends.py -v`
Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add client/luma_denoise/denoisers/__init__.py client/luma_denoise/denoisers/base.py tests/test_denoiser_backends.py
git commit -m "feat(denoisers): add DenoiserBackend base class"
```

---

### Task 2: RenderMan backend

**Files:**
- Create: `client/luma_denoise/denoisers/renderman.py`
- Test: `tests/test_denoiser_backends.py` (append)

The USD/resolution helpers (`detect_large_image`, `get_expected_resolution`, `iter_render_products`, `_count_custom_frames`) move here from `luma_denoise_publish.py` (deleted from the plugin in Task 8). `detect_large_image` imports `hou`/`pxr` at call time, so importing the module stays safe outside Houdini; tests monkeypatch it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_denoiser_backends.py`:

```python
from denoisers.renderman import RendermanDenoiser  # noqa: E402


RM_SETTINGS = {
    "python_executable": "/usr/bin/python3",
    "renderman": {
        "rmantree_path": "/opt/pixar/RenderManProServer-26.3",
        "denoise_exe": "denoise_batch",
        "pixar_license": "9010@192.168.35.28",
        "tiled_denoise_threshold": 2048,
        "wrapper_script_path": "L:/scripts/{version}/renderman_denoise.py",
    },
}


def _rm_backend(monkeypatch, large_image=False):
    backend = RendermanDenoiser()
    monkeypatch.setattr(
        backend, "detect_large_image", lambda instance, rm_settings: large_image)
    return backend


def test_renderman_name_and_combine_flag():
    backend = RendermanDenoiser()
    assert backend.name == "renderman"
    assert backend.requires_combine is True
    assert backend.wrapper_filename == "renderman_denoise.py"


def test_renderman_arguments_basic(monkeypatch):
    backend = _rm_backend(monkeypatch)
    instance = make_instance()
    args = backend.get_arguments(instance, RM_SETTINGS)
    assert args.startswith("L:/scripts/")
    assert "--denoise-exe /opt/pixar/RenderManProServer-26.3/bin/denoise_batch" in args
    assert "--input /renders/shot/main/shot_main.1001.exr" in args
    assert "--output-dir /renders/shot/main/denoised" in args
    assert "--frame-start 1001" in args
    assert "--frame-end 1100" in args
    # 100 frames >= 8 -> cross-frame on; not a large image -> no tiles
    assert "--cross-frame" in args
    assert "--tiles" not in args


def test_renderman_arguments_short_range_no_cross_frame(monkeypatch):
    backend = _rm_backend(monkeypatch)
    instance = make_instance(frameStartHandle=1001, frameEndHandle=1004)
    args = backend.get_arguments(instance, RM_SETTINGS)
    assert "--cross-frame" not in args


def test_renderman_arguments_custom_frames_drive_cross_frame(monkeypatch):
    backend = _rm_backend(monkeypatch)
    # Range says 1 frame, custom frames say 10 -> cross-frame on.
    instance = make_instance(
        frameStartHandle=1001, frameEndHandle=1001,
        publish_attributes={"CollectJobInfo": {
            "use_custom_frames": "custom_only",
            "frames": "1001-1010",
        }})
    args = backend.get_arguments(instance, RM_SETTINGS)
    assert "--cross-frame" in args


def test_renderman_arguments_large_image_enables_tiles(monkeypatch):
    backend = _rm_backend(monkeypatch, large_image=True)
    args = backend.get_arguments(make_instance(), RM_SETTINGS)
    assert "--tiles 2 2" in args


def test_renderman_environment():
    backend = RendermanDenoiser()
    env = backend.get_environment(RM_SETTINGS)
    assert env["RMANTREE"] == "/opt/pixar/RenderManProServer-26.3"
    assert env["PIXAR_LICENSE_FILE"] == "9010@192.168.35.28"
    assert env["PATH"] == "/opt/pixar/RenderManProServer-26.3/bin"


def test_renderman_validate_requires_wrapper_path():
    backend = RendermanDenoiser()
    bad = {"renderman": {"wrapper_script_path": ""}}
    with pytest.raises(RuntimeError, match="renderman.wrapper_script_path"):
        backend.validate(make_instance(), bad)


def test_count_custom_frames():
    count = RendermanDenoiser._count_custom_frames
    assert count("1001,1003-1006,1010") == 6
    assert count("1001") == 1
    assert count("") == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_denoiser_backends.py -v`
Expected: new tests FAIL with `ModuleNotFoundError: No module named 'denoisers.renderman'`

- [ ] **Step 3: Write the implementation**

Create `client/luma_denoise/denoisers/renderman.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_denoiser_backends.py -v`
Expected: all PASSED

- [ ] **Step 5: Commit**

```bash
git add client/luma_denoise/denoisers/renderman.py tests/test_denoiser_backends.py
git commit -m "feat(denoisers): add RenderMan backend"
```

---

### Task 3: OIDN backend

**Files:**
- Create: `client/luma_denoise/denoisers/oidn.py`
- Test: `tests/test_denoiser_backends.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_denoiser_backends.py`:

```python
from denoisers.oidn import OidnDenoiser  # noqa: E402


OIDN_SETTINGS = {
    "python_executable": "/usr/bin/python3",
    "oiio_root_path": "/opt/oiio",
    "oiio_exe": "oiiotool",
    "oidn": {
        "oidn_root_path": "/opt/oidn",
        "denoise_exe": "oidnDenoise",
        "wrapper_script_path": "L:/scripts/{version}/oidn_denoise.py",
        "beauty_channel": "beauty",
        "albedo_channel": "albedo",
        "normal_channel": "N",
    },
}


def test_oidn_name_and_combine_flag():
    backend = OidnDenoiser()
    assert backend.name == "oidn"
    assert backend.requires_combine is True
    assert backend.wrapper_filename == "oidn_denoise.py"


def test_oidn_arguments():
    backend = OidnDenoiser()
    args = backend.get_arguments(make_instance(), OIDN_SETTINGS)
    assert args.startswith("L:/scripts/")
    assert "--oidn-exe /opt/oidn/bin/oidnDenoise" in args
    assert "--oiiotool /opt/oiio/bin/oiiotool" in args
    assert "--input /renders/shot/main/shot_main.1001.exr" in args
    assert "--output-dir /renders/shot/main/denoised" in args
    assert "--frame-start 1001" in args
    assert "--frame-end 1100" in args
    assert "--beauty-channel beauty" in args
    assert "--albedo-channel albedo" in args
    assert "--normal-channel N" in args


def test_oidn_environment_prepends_bin():
    backend = OidnDenoiser()
    env = backend.get_environment(OIDN_SETTINGS)
    assert env == {"PATH": "/opt/oidn/bin"}


def test_oidn_validate_requires_wrapper_path():
    backend = OidnDenoiser()
    bad = {"oidn": dict(OIDN_SETTINGS["oidn"], wrapper_script_path="")}
    with pytest.raises(RuntimeError, match="oidn.wrapper_script_path"):
        backend.validate(make_instance(), bad)


def test_oidn_validate_requires_guide_channels():
    backend = OidnDenoiser()
    bad = {"oidn": dict(OIDN_SETTINGS["oidn"], albedo_channel="")}
    with pytest.raises(RuntimeError, match="albedo_channel"):
        backend.validate(make_instance(), bad)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_denoiser_backends.py -v`
Expected: new tests FAIL with `ModuleNotFoundError: No module named 'denoisers.oidn'`

- [ ] **Step 3: Write the implementation**

Create `client/luma_denoise/denoisers/oidn.py`:

```python
"""Intel Open Image Denoise (OIDN) backend."""

from __future__ import annotations

import os

from .base import ADDON_VERSION, DenoiserBackend, quote


class OidnDenoiser(DenoiserBackend):
    """Builds the Deadline job that runs oidn_denoise.py on the farm.

    OIDN cannot read packed multi-channel render EXRs; the wrapper script
    extracts beauty/albedo/normal per frame via oiiotool, runs oidnDenoise,
    and reassembles the denoised frame.
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

        oidn_root = oidn_settings.get("oidn_root_path", "/opt/oidn")
        exe_name = oidn_settings.get("denoise_exe", "oidnDenoise")
        oidn_exe = f"{oidn_root}/bin/{exe_name}"

        # The extraction tool is the same OIIO install the combine step uses.
        oiio_root = settings.get("oiio_root_path", "/opt/oiio")
        oiio_exe = settings.get("oiio_exe", "oiiotool")
        oiiotool = f"{oiio_root}/bin/{oiio_exe}"

        wrapper_path = self._resolve_wrapper_path(settings)

        parts = [
            quote(wrapper_path),
            "--oidn-exe", quote(oidn_exe),
            "--oiiotool", quote(oiiotool),
            "--input", quote(f"{dirname}/{basename}"),
            "--output-dir", quote(f"{dirname}/denoised"),
            "--frame-start", str(frame_start),
            "--frame-end", str(frame_end),
            "--beauty-channel", quote(oidn_settings.get("beauty_channel", "beauty")),
            "--albedo-channel", quote(oidn_settings.get("albedo_channel", "albedo")),
            "--normal-channel", quote(oidn_settings.get("normal_channel", "N")),
            "--addon-version", ADDON_VERSION,
            "--verbose",
        ]
        return " ".join(parts)

    def get_environment(self, settings: dict) -> dict:
        oidn_settings = self._backend_settings(settings)
        env = {}
        oidn_root = oidn_settings.get("oidn_root_path", "")
        if oidn_root:
            env["PATH"] = f"{oidn_root}/bin"
        return env

    def validate(self, instance, settings: dict) -> None:
        self._resolve_wrapper_path(settings)
        oidn_settings = self._backend_settings(settings)
        for field in ("beauty_channel", "albedo_channel", "normal_channel"):
            if not oidn_settings.get(field, ""):
                raise RuntimeError(
                    f"luma-denoise: 'oidn.{field}' is empty. OIDN requires "
                    "the beauty, albedo, and normal layer names to extract "
                    "them from the render EXR. Set them in the luma-denoise "
                    "project settings (OIDN group)."
                )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_denoiser_backends.py -v`
Expected: all PASSED

- [ ] **Step 5: Commit**

```bash
git add client/luma_denoise/denoisers/oidn.py tests/test_denoiser_backends.py
git commit -m "feat(denoisers): add OIDN backend"
```

---

### Task 4: Backend registry

**Files:**
- Modify: `client/luma_denoise/denoisers/__init__.py` (currently empty)
- Test: `tests/test_denoiser_backends.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_denoiser_backends.py`:

```python
import denoisers  # noqa: E402


def test_registry_returns_backend_instances():
    assert denoisers.get_denoiser_backend("renderman").name == "renderman"
    assert denoisers.get_denoiser_backend("oidn").name == "oidn"


def test_registry_unknown_name_raises_with_known_list():
    with pytest.raises(RuntimeError, match="oidn"):
        denoisers.get_denoiser_backend("optix")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_denoiser_backends.py -v`
Expected: new tests FAIL with `AttributeError: module 'denoisers' has no attribute 'get_denoiser_backend'`

- [ ] **Step 3: Write the implementation**

Replace the contents of `client/luma_denoise/denoisers/__init__.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_denoiser_backends.py -v`
Expected: all PASSED

- [ ] **Step 5: Commit**

```bash
git add client/luma_denoise/denoisers/__init__.py tests/test_denoiser_backends.py
git commit -m "feat(denoisers): add backend registry"
```

---

### Task 5: `renderman_denoise.py` farm wrapper

**Files:**
- Create: `client/luma_denoise/scripts/renderman_denoise.py`
- Create: `tests/test_renderman_denoise.py`

The wrapper is standalone by design (like `oiio_combine.py`): no imports from the addon, deployable as a single file to the shared filesystem.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_renderman_denoise.py`:

```python
"""Unit tests for the renderman_denoise farm wrapper."""

import json
import sys
from pathlib import Path

# Make the wrapper importable regardless of how pytest is invoked.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client" / "luma_denoise" / "scripts"))

import renderman_denoise  # noqa: E402


def _args(**overrides):
    base = [
        "--denoise-exe", "/opt/pixar/bin/denoise_batch",
        "--input", "/renders/shot_main.1001.exr",
        "--output-dir", "/renders/denoised",
        "--frame-start", "1001",
        "--frame-end", "1100",
        "--addon-version", "0.2.0",
    ]
    extra = overrides.pop("extra", [])
    return renderman_denoise.parse_args(base + extra)


def test_parse_args_minimal():
    args = _args()
    assert args.denoise_exe == "/opt/pixar/bin/denoise_batch"
    assert args.input == "/renders/shot_main.1001.exr"
    assert args.output_dir == "/renders/denoised"
    assert args.frame_start == 1001
    assert args.frame_end == 1100
    assert args.cross_frame is False
    assert args.tiles is None
    assert args.verbose is False


def test_build_denoise_argv_basic():
    argv = renderman_denoise.build_denoise_argv(_args())
    assert argv == [
        "/opt/pixar/bin/denoise_batch",
        "-a", "0", "-v", "--clean-alpha", "--progress",
        "-o", "/renders/denoised",
        "/renders/shot_main.1001.exr",
        "1001-1100",
    ]


def test_build_denoise_argv_cross_frame_and_tiles():
    argv = renderman_denoise.build_denoise_argv(
        _args(extra=["--cross-frame", "--tiles", "2", "2"]))
    assert "-cf" in argv
    tiles_i = argv.index("--tiles")
    assert argv[tiles_i:tiles_i + 3] == ["--tiles", "2", "2"]


def test_build_manifest_contents():
    manifest = renderman_denoise.build_manifest(_args())
    assert manifest["denoiser"] == "renderman"
    assert manifest["addon_version"] == "0.2.0"
    assert manifest["source_pattern"] == "/renders/shot_main.####.exr"
    assert manifest["output_pattern"] == "/renders/denoised/shot_main.####.exr"
    assert manifest["beauty_channel_map"] == {
        "Ci.r": "R", "Ci.g": "G", "Ci.b": "B", "a.Z": "A"}
    assert manifest["frames"] == [1001, 1100]
    assert "guide_channels" not in manifest


def test_write_manifest_writes_denoise_sidecar(tmp_path):
    manifest = {"denoiser": "renderman"}
    ok = renderman_denoise.write_manifest(
        str(tmp_path / "shot_main.1001.exr"), manifest)
    assert ok is True
    sidecar = tmp_path / "shot_main.denoise.json"
    assert sidecar.is_file()
    assert json.loads(sidecar.read_text()) == manifest


def test_main_propagates_exit_code_and_writes_manifest(tmp_path, monkeypatch):
    calls = []

    class FakeResult:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return FakeResult()

    monkeypatch.setattr(renderman_denoise.subprocess, "run", fake_run)
    rc = renderman_denoise.main([
        "--denoise-exe", "/opt/pixar/bin/denoise_batch",
        "--input", str(tmp_path / "shot_main.1001.exr"),
        "--output-dir", str(tmp_path / "denoised"),
        "--frame-start", "1001",
        "--frame-end", "1100",
        "--addon-version", "0.2.0",
    ])
    assert rc == 0
    assert len(calls) == 1
    assert calls[0][0] == "/opt/pixar/bin/denoise_batch"
    assert (tmp_path / "denoised" / "shot_main.denoise.json").is_file()


def test_main_failure_skips_manifest(tmp_path, monkeypatch):
    class FakeResult:
        returncode = 3
        stdout = ""
        stderr = "license error"

    monkeypatch.setattr(
        renderman_denoise.subprocess, "run", lambda *a, **k: FakeResult())
    rc = renderman_denoise.main([
        "--denoise-exe", "/opt/pixar/bin/denoise_batch",
        "--input", str(tmp_path / "shot_main.1001.exr"),
        "--output-dir", str(tmp_path / "denoised"),
        "--frame-start", "1001",
        "--frame-end", "1100",
    ])
    assert rc == 3
    assert not (tmp_path / "denoised" / "shot_main.denoise.json").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_renderman_denoise.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'renderman_denoise'`

- [ ] **Step 3: Write the implementation**

Create `client/luma_denoise/scripts/renderman_denoise.py`:

```python
"""renderman_denoise.py - Deadline-worker wrapper for Pixar denoise_batch.

Runs once per sequence on a Deadline worker (the denoise job has Frames=1;
denoise_batch processes the whole frame range in one invocation). Invokes
denoise_batch with the flags computed by the submission backend, propagates
its exit code, and on success writes a sequence-level <name>.denoise.json
sidecar next to the denoised frames describing the output channel naming.
The downstream oiio_combine.py wrapper reads that sidecar.

Standalone by design: deploy this single file to a shared filesystem (the
'renderman.wrapper_script_path' luma-denoise setting points at it).

Usage:
    python renderman_denoise.py
        --denoise-exe <path> --input <first-frame.exr> --output-dir <dir>
        --frame-start N --frame-end N
        [--cross-frame] [--tiles X Y] [--addon-version V] [--verbose]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Sequence

_FRAME_RE = re.compile(r"\.(\d{3,})(?=\.[A-Za-z0-9]+$)")

# denoise_batch always emits RenderMan's Ci/a channel convention; the map
# tells the combine step how to rename them for compositing (Nuke R/G/B/A).
# 'a.Z' is appended from the raw render by the combine step, not present in
# the denoised output itself.
RENDERMAN_BEAUTY_MAP = {"Ci.r": "R", "Ci.g": "G", "Ci.b": "B", "a.Z": "A"}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="luma-denoise RenderMan denoise wrapper")
    parser.add_argument("--denoise-exe", required=True, dest="denoise_exe",
                        help="Absolute path to denoise_batch.")
    parser.add_argument("--input", required=True,
                        help="Path to the first frame of the raw sequence.")
    parser.add_argument("--output-dir", required=True, dest="output_dir",
                        help="Directory to write denoised frames into.")
    parser.add_argument("--frame-start", required=True, type=int,
                        dest="frame_start")
    parser.add_argument("--frame-end", required=True, type=int,
                        dest="frame_end")
    parser.add_argument("--cross-frame", action="store_true",
                        dest="cross_frame",
                        help="Enable cross-frame denoising (-cf).")
    parser.add_argument("--tiles", nargs=2, type=int, default=None,
                        metavar=("X", "Y"),
                        help="Enable tiled denoising with X x Y tiles.")
    parser.add_argument("--addon-version", default="unknown",
                        dest="addon_version",
                        help="luma-denoise addon version, recorded in the manifest.")
    parser.add_argument("--verbose", "-v", action="store_true")
    return parser.parse_args(argv)


def build_denoise_argv(args: argparse.Namespace) -> list:
    argv = [args.denoise_exe, "-a", "0", "-v", "--clean-alpha", "--progress"]
    if args.cross_frame:
        argv.append("-cf")
    if args.tiles:
        argv.extend(["--tiles", str(args.tiles[0]), str(args.tiles[1])])
    argv.extend(["-o", args.output_dir, args.input,
                 f"{args.frame_start}-{args.frame_end}"])
    return argv


def _strip_frame_token(path: str) -> str:
    """Replace '.NNNN.' frame digits in a path with '.####.' tokens."""
    return _FRAME_RE.sub(lambda m: "." + "#" * len(m.group(1)), path)


def build_manifest(args: argparse.Namespace) -> dict:
    basename = os.path.basename(args.input)
    output_pattern = "/".join(
        [args.output_dir.replace("\\", "/").rstrip("/"), basename])
    return {
        "denoiser": "renderman",
        "addon_version": args.addon_version,
        "source_pattern": _strip_frame_token(args.input.replace("\\", "/")),
        "output_pattern": _strip_frame_token(output_pattern),
        "beauty_channel_map": dict(RENDERMAN_BEAUTY_MAP),
        "frames": [args.frame_start, args.frame_end],
    }


def write_manifest(output_path: str, manifest: dict) -> bool:
    """Write a sequence-level <name>.denoise.json sidecar.

    Atomic temp-file rename, same pattern as oiio_combine.py's manifest:
    never corrupt, last writer wins, never raises.
    """
    p = Path(output_path)
    name = p.name
    m = _FRAME_RE.search(name)
    base = name[:m.start()] if m else p.stem
    sidecar = p.parent / f"{base}.denoise.json"
    tmp = sidecar.with_name(f".{sidecar.name}.{os.getpid()}.tmp")
    try:
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(manifest, indent=2))
        os.replace(tmp, sidecar)
        return True
    except Exception as e:
        print(f"[renderman_denoise] WARN: could not write manifest "
              f"{sidecar}: {e}", file=sys.stderr)
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    denoise_argv = build_denoise_argv(args)

    if args.verbose:
        print(f"[renderman_denoise] running: {' '.join(denoise_argv)}")

    result = subprocess.run(
        denoise_argv, capture_output=True, text=True, check=False)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)

    if result.returncode != 0:
        print(f"[renderman_denoise] ERROR: denoise_batch exited with "
              f"{result.returncode}", file=sys.stderr)
        return result.returncode

    manifest = build_manifest(args)
    sidecar_anchor = os.path.join(
        args.output_dir, os.path.basename(args.input))
    write_manifest(sidecar_anchor, manifest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_renderman_denoise.py -v`
Expected: 7 PASSED

- [ ] **Step 5: Commit**

```bash
git add client/luma_denoise/scripts/renderman_denoise.py tests/test_renderman_denoise.py
git commit -m "feat(scripts): add renderman_denoise farm wrapper with manifest"
```

---

### Task 6: `oidn_denoise.py` farm wrapper

**Files:**
- Create: `client/luma_denoise/scripts/oidn_denoise.py`
- Create: `tests/test_oidn_denoise.py`

The channel-reading block intentionally mirrors `oiio_combine.py` rather than importing it — farm wrappers are standalone single files by design (deployment copies individual files; no shared module).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_oidn_denoise.py`:

```python
"""Unit tests for the oidn_denoise farm wrapper."""

import json
import sys
from pathlib import Path

import pytest

# Make the wrapper importable regardless of how pytest is invoked.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client" / "luma_denoise" / "scripts"))

import oidn_denoise  # noqa: E402


CHANNELS = [
    "beauty.r", "beauty.g", "beauty.b",
    "albedo.r", "albedo.g", "albedo.b",
    "N.x", "N.y", "N.z",
    "a.Z", "depth.Z",
    "CryptoObject00.r", "CryptoObject00.g", "CryptoObject00.b",
]


def _argv(tmp_path, **overrides):
    argv = [
        "--oidn-exe", "/opt/oidn/bin/oidnDenoise",
        "--oiiotool", "/opt/oiio/bin/oiiotool",
        "--input", str(tmp_path / "shot_main.1001.exr"),
        "--output-dir", str(tmp_path / "denoised"),
        "--frame-start", "1001",
        "--frame-end", "1002",
        "--beauty-channel", "beauty",
        "--albedo-channel", "albedo",
        "--normal-channel", "N",
        "--addon-version", "0.2.0",
    ]
    return argv


def test_parse_args():
    args = oidn_denoise.parse_args(_argv(Path("/renders")))
    assert args.oidn_exe == "/opt/oidn/bin/oidnDenoise"
    assert args.beauty_channel == "beauty"
    assert args.albedo_channel == "albedo"
    assert args.normal_channel == "N"
    assert args.frame_start == 1001
    assert args.frame_end == 1002
    assert args.keep_temps is False


def test_frame_path_substitutes_frame_number():
    assert oidn_denoise.frame_path("/r/shot.1001.exr", 1005) == "/r/shot.1005.exr"
    assert oidn_denoise.frame_path("/r/shot.0099.exr", 100) == "/r/shot.0100.exr"


def test_frame_path_without_frame_digits_raises():
    with pytest.raises(RuntimeError, match="frame digits"):
        oidn_denoise.frame_path("/r/shot.exr", 1001)


def test_layer_channels_groups_by_prefix():
    assert oidn_denoise.layer_channels(CHANNELS, "beauty") == [
        "beauty.r", "beauty.g", "beauty.b"]
    assert oidn_denoise.layer_channels(CHANNELS, "N") == ["N.x", "N.y", "N.z"]
    assert oidn_denoise.layer_channels(CHANNELS, "missing") == []


def test_require_layer_missing_raises_actionable_error():
    with pytest.raises(RuntimeError) as excinfo:
        oidn_denoise.require_layer(CHANNELS, "diffuse", "albedo guide",
                                   "oidn.albedo_channel")
    message = str(excinfo.value)
    assert "diffuse" in message
    assert "oidn.albedo_channel" in message


def test_build_frame_commands_sequence(tmp_path):
    args = oidn_denoise.parse_args(_argv(tmp_path))
    in_path = str(tmp_path / "shot_main.1001.exr")
    out_path = str(tmp_path / "denoised" / "shot_main.1001.exr")
    beauty, cmds = oidn_denoise.build_frame_commands(
        args, in_path, out_path, CHANNELS, str(tmp_path / "tmp"))

    assert beauty == ["beauty.r", "beauty.g", "beauty.b"]
    assert len(cmds) == 5
    # 3 extractions with the right channel groups
    assert cmds[0][0] == "/opt/oiio/bin/oiiotool"
    assert "beauty.r,beauty.g,beauty.b" in cmds[0]
    assert "albedo.r,albedo.g,albedo.b" in cmds[1]
    assert "N.x,N.y,N.z" in cmds[2]
    # oidnDenoise with guides
    assert cmds[3][0] == "/opt/oidn/bin/oidnDenoise"
    assert "--hdr" in cmds[3] and "--alb" in cmds[3] and "--nrm" in cmds[3]
    # reassembly renames back to the original beauty channel names
    assert cmds[4][0] == "/opt/oiio/bin/oiiotool"
    assert "--chnames" in cmds[4]
    assert "beauty.r,beauty.g,beauty.b" in cmds[4]
    assert cmds[4][-1] == out_path


def test_build_frame_commands_missing_guide_raises(tmp_path):
    args = oidn_denoise.parse_args(_argv(tmp_path))
    no_albedo = [ch for ch in CHANNELS if not ch.startswith("albedo.")]
    with pytest.raises(RuntimeError, match="oidn.albedo_channel"):
        oidn_denoise.build_frame_commands(
            args, "/r/in.exr", "/r/out.exr", no_albedo, "/tmp/x")


def test_build_manifest_contents(tmp_path):
    args = oidn_denoise.parse_args(_argv(tmp_path))
    manifest = oidn_denoise.build_manifest(
        args, ["beauty.r", "beauty.g", "beauty.b"])
    assert manifest["denoiser"] == "oidn"
    assert manifest["addon_version"] == "0.2.0"
    assert manifest["beauty_channel_map"] == {
        "beauty.r": "R", "beauty.g": "G", "beauty.b": "B", "a.Z": "A"}
    assert manifest["guide_channels"] == {"albedo": "albedo", "normal": "N"}
    assert manifest["frames"] == [1001, 1002]
    assert "####" in manifest["source_pattern"]
    assert "####" in manifest["output_pattern"]


def test_main_runs_all_frames_and_writes_manifest(tmp_path, monkeypatch):
    calls = []

    class FakeResult:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(
        oidn_denoise, "read_channels", lambda path, oiiotool: list(CHANNELS))
    monkeypatch.setattr(
        oidn_denoise.subprocess, "run",
        lambda argv, **kw: calls.append(argv) or FakeResult())

    rc = oidn_denoise.main(_argv(tmp_path))
    assert rc == 0
    # 2 frames x 5 commands
    assert len(calls) == 10
    sidecar = tmp_path / "denoised" / "shot_main.denoise.json"
    assert sidecar.is_file()
    assert json.loads(sidecar.read_text())["denoiser"] == "oidn"


def test_main_command_failure_aborts_with_exit_code(tmp_path, monkeypatch):
    class FakeResult:
        returncode = 9
        stdout = ""
        stderr = "oidn error"

    monkeypatch.setattr(
        oidn_denoise, "read_channels", lambda path, oiiotool: list(CHANNELS))
    monkeypatch.setattr(
        oidn_denoise.subprocess, "run", lambda argv, **kw: FakeResult())

    rc = oidn_denoise.main(_argv(tmp_path))
    assert rc == 9
    assert not (tmp_path / "denoised" / "shot_main.denoise.json").exists()


def test_main_missing_guide_channel_fails_loudly(tmp_path, monkeypatch, capsys):
    no_normal = [ch for ch in CHANNELS if not ch.startswith("N.")]
    monkeypatch.setattr(
        oidn_denoise, "read_channels", lambda path, oiiotool: no_normal)

    rc = oidn_denoise.main(_argv(tmp_path))
    assert rc == 1
    captured = capsys.readouterr()
    assert "oidn.normal_channel" in captured.err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_oidn_denoise.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'oidn_denoise'`

- [ ] **Step 3: Write the implementation**

Create `client/luma_denoise/scripts/oidn_denoise.py`:

```python
"""oidn_denoise.py - Deadline-worker wrapper for Intel Open Image Denoise.

OIDN is a color-buffer denoiser: its oidnDenoise CLI takes one 3-channel
image file plus albedo/normal guides as SEPARATE files, and cannot select
channels out of a packed multi-channel render EXR. This wrapper bridges that
gap per frame:

    1. read the raw EXR channel list
    2. hard-fail if the beauty/albedo/normal layers are missing (guides are
       REQUIRED for quality; this is a deliberate pipeline policy)
    3. extract each layer to a temp single-layer EXR via oiiotool
    4. run oidnDenoise --hdr beauty --alb albedo --nrm normal
    5. rename the denoised channels back to the original beauty names and
       write the frame into the denoised output directory

After the last frame a sequence-level <name>.denoise.json sidecar is written
describing the output channel naming; oiio_combine.py reads it downstream.

Standalone by design: deploy this single file to a shared filesystem (the
'oidn.wrapper_script_path' luma-denoise setting points at it). The channel
reading block mirrors oiio_combine.py on purpose - wrappers do not import
each other.

Usage:
    python oidn_denoise.py
        --oidn-exe <path> --oiiotool <path>
        --input <first-frame.exr> --output-dir <dir>
        --frame-start N --frame-end N
        --beauty-channel beauty --albedo-channel albedo --normal-channel N
        [--addon-version V] [--keep-temps] [--verbose]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence

_FRAME_RE = re.compile(r"\.(\d{3,})(?=\.[A-Za-z0-9]+$)")

# The combine step appends 'a.Z' (alpha) from the raw render; map it to A.
DEFAULT_ALPHA_RENAME = {"a.Z": "A"}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="luma-denoise OIDN denoise wrapper")
    parser.add_argument("--oidn-exe", required=True, dest="oidn_exe",
                        help="Absolute path to oidnDenoise.")
    parser.add_argument("--oiiotool", required=True,
                        help="Absolute path to oiiotool (channel extraction).")
    parser.add_argument("--input", required=True,
                        help="Path to the first frame of the raw sequence.")
    parser.add_argument("--output-dir", required=True, dest="output_dir",
                        help="Directory to write denoised frames into.")
    parser.add_argument("--frame-start", required=True, type=int,
                        dest="frame_start")
    parser.add_argument("--frame-end", required=True, type=int,
                        dest="frame_end")
    parser.add_argument("--beauty-channel", default="beauty",
                        dest="beauty_channel",
                        help="Layer name of the beauty channels in the raw EXR.")
    parser.add_argument("--albedo-channel", default="albedo",
                        dest="albedo_channel",
                        help="Layer name of the albedo guide (REQUIRED in EXR).")
    parser.add_argument("--normal-channel", default="N",
                        dest="normal_channel",
                        help="Layer name of the normal guide (REQUIRED in EXR).")
    parser.add_argument("--addon-version", default="unknown",
                        dest="addon_version")
    parser.add_argument("--keep-temps", action="store_true", dest="keep_temps",
                        help="Keep per-frame temp EXRs for debugging.")
    parser.add_argument("--verbose", "-v", action="store_true")
    return parser.parse_args(argv)


# -- channel reading (mirrors oiio_combine.py; standalone on purpose) -----

_CHANNEL_LIST_RE = re.compile(r"^\s*channel list:\s*(.+)\s*$", re.MULTILINE)
_CHANNEL_TOKEN_RE = re.compile(
    r"([A-Za-z_][A-Za-z0-9_.]*)(?:\s*\([A-Za-z0-9]+\))?")


def _parse_oiiotool_info_channels(stdout: str) -> list:
    match = _CHANNEL_LIST_RE.search(stdout)
    if not match:
        raise RuntimeError(
            "oiiotool --info output did not contain a 'channel list:' line")
    channels = []
    for token in match.group(1).split(","):
        m = _CHANNEL_TOKEN_RE.search(token.strip())
        if m:
            channels.append(m.group(1))
    return channels


def _read_channels_subprocess(path: str, oiiotool_path: str) -> list:
    result = subprocess.run(
        [oiiotool_path, "--info", "-v", path],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"oiiotool --info -v {path} failed with exit {result.returncode}: "
            f"{result.stderr.strip()}"
        )
    return _parse_oiiotool_info_channels(result.stdout)


def _try_import_oiio():
    try:
        import OpenImageIO  # type: ignore
        return OpenImageIO
    except Exception:
        return None


def read_channels(path: str, oiiotool_path: str) -> list:
    """Read channel names with OIIO Python preferred, subprocess fallback."""
    oiio_module = _try_import_oiio()
    if oiio_module is not None:
        inp = oiio_module.ImageInput.open(path)
        if inp is None:
            raise RuntimeError(f"OIIO could not open EXR: {path}")
        try:
            return list(inp.spec().channelnames)
        finally:
            inp.close()
    return _read_channels_subprocess(path, oiiotool_path)


# -- frame and layer helpers ----------------------------------------------

def frame_path(template_path: str, frame: int) -> str:
    """Return template_path with its frame digits replaced by `frame`."""
    dirname = os.path.dirname(template_path)
    basename = os.path.basename(template_path)

    def _sub(m):
        return "." + str(frame).zfill(len(m.group(1)))

    new_name, n = _FRAME_RE.subn(_sub, basename)
    if n == 0:
        raise RuntimeError(
            f"Could not find frame digits in filename: {template_path}")
    return f"{dirname}/{new_name}".replace("\\", "/") if dirname else new_name


def layer_channels(channels: list, layer: str) -> list:
    """Channels belonging to a layer: 'beauty' -> beauty.r/beauty.g/..."""
    grouped = [ch for ch in channels if ch.startswith(layer + ".")]
    if grouped:
        return grouped
    return [ch for ch in channels if ch == layer]


def require_layer(channels: list, layer: str, role: str,
                  settings_field: str) -> list:
    """Return the first 3 channels of a layer or raise an actionable error."""
    found = layer_channels(channels, layer)
    if len(found) < 3:
        raise RuntimeError(
            f"OIDN requires a 3-channel {role} layer but layer '{layer}' "
            f"has {len(found)} channel(s) in the input EXR "
            f"(found: {found or 'none'}). Either the render is missing the "
            f"AOV or the layer name is wrong - configure it via the "
            f"luma-denoise settings field '{settings_field}'."
        )
    return found[:3]


def build_frame_commands(args: argparse.Namespace, in_path: str,
                         out_path: str, channels: list,
                         tmpdir: str):
    """Return (beauty_channels, [argv, ...]) for one frame.

    Command sequence: extract beauty -> extract albedo -> extract normal ->
    oidnDenoise -> rename denoised channels back and write the output frame.
    """
    beauty = require_layer(
        channels, args.beauty_channel, "beauty", "oidn.beauty_channel")
    albedo = require_layer(
        channels, args.albedo_channel, "albedo guide", "oidn.albedo_channel")
    normal = require_layer(
        channels, args.normal_channel, "normal guide", "oidn.normal_channel")

    def t(name):
        return f"{tmpdir}/{name}".replace("\\", "/")

    cmds = [
        [args.oiiotool, in_path, "--ch", ",".join(beauty),
         "-o", t("beauty.exr")],
        [args.oiiotool, in_path, "--ch", ",".join(albedo),
         "-o", t("albedo.exr")],
        [args.oiiotool, in_path, "--ch", ",".join(normal),
         "-o", t("normal.exr")],
        [args.oidn_exe, "--hdr", t("beauty.exr"),
         "--alb", t("albedo.exr"), "--nrm", t("normal.exr"),
         "-o", t("denoised.exr")],
        [args.oiiotool, t("denoised.exr"),
         "--chnames", ",".join(beauty),
         "-o", out_path],
    ]
    return beauty, cmds


def _strip_frame_token(path: str) -> str:
    return _FRAME_RE.sub(lambda m: "." + "#" * len(m.group(1)), path)


def build_manifest(args: argparse.Namespace, beauty_channels: list) -> dict:
    basename = os.path.basename(args.input)
    output_pattern = "/".join(
        [args.output_dir.replace("\\", "/").rstrip("/"), basename])
    beauty_map = {}
    if len(beauty_channels) >= 3:
        beauty_map = {
            beauty_channels[0]: "R",
            beauty_channels[1]: "G",
            beauty_channels[2]: "B",
        }
    beauty_map.update(DEFAULT_ALPHA_RENAME)
    return {
        "denoiser": "oidn",
        "addon_version": args.addon_version,
        "source_pattern": _strip_frame_token(args.input.replace("\\", "/")),
        "output_pattern": _strip_frame_token(output_pattern),
        "beauty_channel_map": beauty_map,
        "guide_channels": {
            "albedo": args.albedo_channel,
            "normal": args.normal_channel,
        },
        "frames": [args.frame_start, args.frame_end],
    }


def write_manifest(output_path: str, manifest: dict) -> bool:
    """Write a sequence-level <name>.denoise.json sidecar (atomic, never raises)."""
    p = Path(output_path)
    name = p.name
    m = _FRAME_RE.search(name)
    base = name[:m.start()] if m else p.stem
    sidecar = p.parent / f"{base}.denoise.json"
    tmp = sidecar.with_name(f".{sidecar.name}.{os.getpid()}.tmp")
    try:
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(manifest, indent=2))
        os.replace(tmp, sidecar)
        return True
    except Exception as e:
        print(f"[oidn_denoise] WARN: could not write manifest {sidecar}: {e}",
              file=sys.stderr)
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


def _run_commands(cmds: list, verbose: bool) -> int:
    for argv in cmds:
        if verbose:
            print(f"[oidn_denoise] running: {' '.join(argv)}")
        result = subprocess.run(
            argv, capture_output=True, text=True, check=False)
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        if result.returncode != 0:
            print(f"[oidn_denoise] ERROR: '{argv[0]}' exited with "
                  f"{result.returncode}", file=sys.stderr)
            return result.returncode
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        return _run(args)
    except RuntimeError as exc:
        print(f"[oidn_denoise] ERROR: {exc}", file=sys.stderr)
        return 1


def _run(args: argparse.Namespace) -> int:
    output_dir = args.output_dir.replace("\\", "/")
    os.makedirs(output_dir, exist_ok=True)

    beauty_channels = []
    for frame in range(args.frame_start, args.frame_end + 1):
        in_path = frame_path(args.input.replace("\\", "/"), frame)
        out_path = f"{output_dir}/{os.path.basename(in_path)}"

        channels = read_channels(in_path, args.oiiotool)
        if args.verbose:
            print(f"[oidn_denoise] frame {frame}: "
                  f"{len(channels)} channels in {in_path}")

        tmpdir = tempfile.mkdtemp(prefix=f"oidn_{frame}_")
        try:
            beauty_channels, cmds = build_frame_commands(
                args, in_path, out_path, channels, tmpdir)
            rc = _run_commands(cmds, args.verbose)
            if rc != 0:
                return rc
        finally:
            if not args.keep_temps:
                shutil.rmtree(tmpdir, ignore_errors=True)

    manifest = build_manifest(args, beauty_channels)
    sidecar_anchor = f"{output_dir}/{os.path.basename(args.input)}"
    write_manifest(sidecar_anchor, manifest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_oidn_denoise.py -v`
Expected: 11 PASSED

- [ ] **Step 5: Commit**

```bash
git add client/luma_denoise/scripts/oidn_denoise.py tests/test_oidn_denoise.py
git commit -m "feat(scripts): add oidn_denoise farm wrapper with required guides"
```

---

### Task 7: `oiio_combine.py` reads the denoise manifest

**Files:**
- Modify: `client/luma_denoise/scripts/oiio_combine.py`
- Test: `tests/test_oiio_combine.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_oiio_combine.py`:

```python
# --- denoise manifest reading -------------------------------------------


def test_denoise_sidecar_path_strips_frame_token():
    p = oiio_combine._denoise_sidecar_path("/d/denoised/shot.1001.exr")
    assert str(p).replace("\\", "/") == "/d/denoised/shot.denoise.json"


def test_load_denoise_manifest_missing_returns_none(tmp_path):
    result = oiio_combine.load_denoise_manifest(
        str(tmp_path / "shot.1001.exr"))
    assert result is None


def test_load_denoise_manifest_reads_sidecar(tmp_path):
    sidecar = tmp_path / "shot.denoise.json"
    sidecar.write_text(json.dumps({
        "denoiser": "oidn",
        "beauty_channel_map": {"beauty.r": "R"},
    }))
    result = oiio_combine.load_denoise_manifest(str(tmp_path / "shot.1001.exr"))
    assert result["denoiser"] == "oidn"
    assert result["beauty_channel_map"] == {"beauty.r": "R"}


def test_load_denoise_manifest_corrupt_returns_none(tmp_path, capsys):
    (tmp_path / "shot.denoise.json").write_text("{not json")
    result = oiio_combine.load_denoise_manifest(str(tmp_path / "shot.1001.exr"))
    assert result is None
    assert "WARN" in capsys.readouterr().err


def test_resolve_rename_map_prefers_manifest(tmp_path):
    sidecar = tmp_path / "shot.denoise.json"
    sidecar.write_text(json.dumps({
        "denoiser": "oidn",
        "beauty_channel_map": {"beauty.r": "R", "a.Z": "A"},
    }))
    rename_map = oiio_combine.resolve_rename_map(
        denoised_path=str(tmp_path / "shot.1001.exr"),
        cli_rename_pairs=["Ci.r=R", "Ci.g=G"],
        pass_through=False,
        verbose=False,
    )
    assert rename_map == {"beauty.r": "R", "a.Z": "A"}


def test_resolve_rename_map_falls_back_to_cli(tmp_path):
    rename_map = oiio_combine.resolve_rename_map(
        denoised_path=str(tmp_path / "shot.1001.exr"),
        cli_rename_pairs=["Ci.r=R", "Ci.g=G"],
        pass_through=False,
        verbose=False,
    )
    assert rename_map == {"Ci.r": "R", "Ci.g": "G"}


def test_resolve_rename_map_pass_through_ignores_manifest(tmp_path):
    sidecar = tmp_path / "shot.denoise.json"
    sidecar.write_text(json.dumps({
        "beauty_channel_map": {"beauty.r": "R"},
    }))
    rename_map = oiio_combine.resolve_rename_map(
        denoised_path=str(tmp_path / "shot.1001.exr"),
        cli_rename_pairs=["beauty.r=R"],
        pass_through=True,
        verbose=False,
    )
    assert rename_map == {"beauty.r": "R"}
```

Also add `import json` to the imports at the top of `tests/test_oiio_combine.py` if it is not already imported.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_oiio_combine.py -v`
Expected: new tests FAIL with `AttributeError: module 'oiio_combine' has no attribute '_denoise_sidecar_path'`

- [ ] **Step 3: Write the implementation**

In `client/luma_denoise/scripts/oiio_combine.py`, add after `_sequence_sidecar_path` (around line 314):

```python
def _denoise_sidecar_path(denoised_path: str) -> Path:
    """Derive the denoise-manifest path from a per-frame denoised path.

    '<dir>/<name>.<NNNN>.<ext>' -> '<dir>/<name>.denoise.json'
    Written by the denoise wrappers (renderman_denoise.py / oidn_denoise.py).
    """
    p = Path(denoised_path)
    name = p.name
    m = _FRAME_RE.search(name)
    base = name[:m.start()] if m else p.stem
    return p.parent / f"{base}.denoise.json"


def load_denoise_manifest(denoised_path: str) -> dict | None:
    """Load the denoise sidecar next to the denoised frames, if present.

    Returns None when the sidecar is missing or unreadable (warned to
    stderr) - the caller falls back to the CLI rename pairs.
    """
    sidecar = _denoise_sidecar_path(denoised_path)
    if not sidecar.is_file():
        return None
    try:
        return json.loads(sidecar.read_text())
    except Exception as e:
        print(f"[oiio_combine] WARN: unreadable denoise manifest "
              f"{sidecar}: {e}", file=sys.stderr)
        return None


def resolve_rename_map(denoised_path: str, cli_rename_pairs: list[str],
                       pass_through: bool, verbose: bool) -> dict[str, str]:
    """Resolve the beauty rename map: denoise manifest wins over CLI pairs.

    The manifest is authoritative because the denoise wrapper knows exactly
    which channel names it wrote; the CLI pairs come from static settings
    and remain as the fallback (old renders, pass-through mode).
    """
    cli_map = parse_rename_pairs(cli_rename_pairs)
    if pass_through:
        return cli_map
    manifest = load_denoise_manifest(denoised_path)
    if manifest:
        manifest_map = manifest.get("beauty_channel_map") or {}
        if manifest_map:
            if verbose:
                print(f"[oiio_combine] rename map from denoise manifest "
                      f"({manifest.get('denoiser', '?')}): {manifest_map}")
            return {str(k): str(v) for k, v in manifest_map.items()}
    return cli_map
```

Then in `_run()`, replace the line:

```python
    rename_map = parse_rename_pairs(args.rename)
```

with:

```python
    rename_map = resolve_rename_map(
        denoised_path=args.denoised,
        cli_rename_pairs=args.rename,
        pass_through=pass_through,
        verbose=args.verbose,
    )
```

(Note: `parse_rename_pairs` raises `ValueError` on malformed pairs; `resolve_rename_map` calls it first, so CLI validation still happens even when the manifest wins — behavior preserved.)

- [ ] **Step 4: Run the full combine test file**

Run: `python -m pytest tests/test_oiio_combine.py -v`
Expected: all PASSED (old tests unaffected — `load_denoise_manifest` returns None in their tmp dirs)

- [ ] **Step 5: Commit**

```bash
git add client/luma_denoise/scripts/oiio_combine.py tests/test_oiio_combine.py
git commit -m "feat(oiio_combine): read denoise manifest for beauty rename map"
```

---

### Task 8: Server settings restructure

**Files:**
- Modify: `server/settings.py`

No pytest here — `ayon_server` is not installed locally. Verification is `py_compile` (syntax) plus careful review; the real check is the AYON server loading the addon after deploy.

- [ ] **Step 1: Replace the denoise fields**

In `server/settings.py`, **delete** these fields from `LumaDenoiseSettings` (lines 54–98 in the current file): `denoise_deadline_priority`, `denoise_pool`, `denoise_group` stay; **remove** `denoise_rmantree_path`, `denoise_exe`, `denoise_pixar_lic`, `tiled_denoise_threshold`.

**Add** these model classes after `ChannelRenamePair` (before `LumaDenoiseSettings`):

```python
def _denoiser_enum():
    return [
        {"value": "renderman", "label": "Pixar RenderMan (denoise_batch)"},
        {"value": "oidn", "label": "Intel Open Image Denoise (OIDN)"},
    ]


class RendermanDenoiserSettings(BaseSettingsModel):
    """Pixar RenderMan denoise_batch backend."""

    rmantree_path: str = SettingsField(
        "/opt/pixar/RenderManProServer-26.3",
        title="RenderMan Root Path",
        description="Path to RMANTREE on the Deadline workers.",
    )

    denoise_exe: str = SettingsField(
        "denoise_batch",
        title="Denoiser Executable Name",
        description="Name of the RenderMan denoiser executable in <RMANTREE>/bin.",
    )

    pixar_license: str = SettingsField(
        "9010@192.168.35.28",
        title="RenderMan License Server",
        description="RenderMan license server or file location.",
    )

    tiled_denoise_threshold: int = SettingsField(
        2048,
        title="Tiled Denoise Resolution Threshold",
        description=(
            "Minimum resolution (width or height) at which tiled denoising "
            "is enabled. Images with either dimension at or above this value "
            "will be denoised in tiles to reduce memory usage."
        ),
    )

    wrapper_script_path: str = SettingsField(
        "",
        title="Wrapper Script Path",
        description=(
            "Absolute path to renderman_denoise.py on a shared filesystem "
            "accessible from every Deadline render node. Supports the "
            "{version} token - substituted at submission time with the "
            "luma-denoise addon version. MUST be configured for the "
            "RenderMan denoise step to submit."
        ),
    )


class OidnDenoiserSettings(BaseSettingsModel):
    """Intel Open Image Denoise backend."""

    oidn_root_path: str = SettingsField(
        "/opt/oidn",
        title="OIDN Root Path",
        description="Path to the OIDN install root on the Deadline workers.",
    )

    denoise_exe: str = SettingsField(
        "oidnDenoise",
        title="Denoiser Executable Name",
        description="Name of the OIDN executable in <root>/bin.",
    )

    wrapper_script_path: str = SettingsField(
        "",
        title="Wrapper Script Path",
        description=(
            "Absolute path to oidn_denoise.py on a shared filesystem "
            "accessible from every Deadline render node. Supports the "
            "{version} token. MUST be configured for the OIDN denoise "
            "step to submit."
        ),
    )

    beauty_channel: str = SettingsField(
        "beauty",
        title="Beauty Layer Name",
        description=(
            "Layer name of the beauty channels in the raw render EXR "
            "(e.g. 'beauty' for beauty.r/beauty.g/beauty.b)."
        ),
    )

    albedo_channel: str = SettingsField(
        "albedo",
        title="Albedo Guide Layer Name",
        description=(
            "Layer name of the albedo guide AOV. REQUIRED: the OIDN "
            "denoise job fails if this layer is missing from the render."
        ),
    )

    normal_channel: str = SettingsField(
        "N",
        title="Normal Guide Layer Name",
        description=(
            "Layer name of the normal guide AOV. REQUIRED: the OIDN "
            "denoise job fails if this layer is missing from the render."
        ),
    )
```

**Add** these fields to `LumaDenoiseSettings`, directly after `denoise_group`:

```python
    denoiser: str = SettingsField(
        "renderman",
        title="Denoiser",
        description="Which denoiser backend processes the rendered EXRs.",
        enum_resolver=_denoiser_enum,
    )

    renderman: RendermanDenoiserSettings = SettingsField(
        default_factory=RendermanDenoiserSettings,
        title="RenderMan Denoiser",
        description="Settings for the Pixar RenderMan denoise_batch backend.",
    )

    oidn: OidnDenoiserSettings = SettingsField(
        default_factory=OidnDenoiserSettings,
        title="OIDN Denoiser",
        description="Settings for the Intel Open Image Denoise backend.",
    )
```

- [ ] **Step 2: Verify syntax**

Run: `python -m py_compile server/settings.py`
Expected: exit 0, no output. (This checks syntax only — `ayon_server` imports are not executed.)

- [ ] **Step 3: Verify no stale references to removed fields**

Run: `git grep -n "denoise_rmantree_path\|denoise_pixar_lic" -- server client`
Expected: hits ONLY in `client/luma_denoise/plugins/publish/houdini/luma_denoise_publish.py` (fixed in Task 9). (`tiled_denoise_threshold` is NOT in this grep — it legitimately lives on in the nested settings group and the RenderMan backend.)

- [ ] **Step 4: Commit**

```bash
git add server/settings.py
git commit -m "feat(settings)!: denoiser dropdown with nested renderman/oidn groups"
```

---

### Task 9: Rewire `LumaDenoiseUsdRender` to the backend abstraction

**Files:**
- Modify: `client/luma_denoise/plugins/publish/houdini/luma_denoise_publish.py`

No pytest (pyblish/ayon/hou imports unavailable locally) — verification is `py_compile`, the grep from Task 8 Step 3 coming back clean, and the full existing suite still passing.

- [ ] **Step 1: Replace the module imports**

Replace lines 1–13 (imports) with:

```python
import os
from copy import deepcopy
from dataclasses import dataclass, field, asdict
import pyblish.api

from ayon_core.pipeline import AYONPyblishPluginMixin
from ayon_deadline import abstract_submit_deadline

from luma_denoise.denoisers import get_denoiser_backend
```

(`ayon_houdini.api.usd` imports move with the USD helpers into the RenderMan backend — already done in Task 2.)

- [ ] **Step 2: Update the class docstring and `get_job_info`**

Replace the class docstring:

```python
    """Submit the denoise pass for USD-rendered EXR sequences to Deadline.

    The denoiser backend (RenderMan denoise_batch or Intel OIDN) is selected
    by the 'denoiser' luma-denoise project setting. Each backend runs as a
    Python wrapper script on the worker and writes a <seq>.denoise.json
    sidecar that the downstream OIIO combine job reads.
    """
```

In `get_job_info`, replace:

```python
        job_name = "{scene} - {instance} [DENOISE]".format(scene=scenename, instance=instance.name)
```

with:

```python
        backend_tag = self._backend.name.upper()
        job_name = "{scene} - {instance} [DENOISE:{tag}]".format(
            scene=scenename, instance=instance.name, tag=backend_tag)
```

The rest of `get_job_info` (priority/pool/group from `denoise_settings`, dependencies, `Frames = 1`, output dir) is unchanged.

- [ ] **Step 3: Replace `get_plugin_info` entirely**

Replace the whole `get_plugin_info` method (currently ~90 lines of RenderMan arg building) with:

```python
    def get_plugin_info(self, job_type=None):
        instance = self._instance
        denoise_settings = (
            instance.context.data["project_settings"]["luma-denoise"])

        plugin_info = CommandLinePluginInfo(
            Executable=self._backend.get_executable(denoise_settings),
            Arguments=self._backend.get_arguments(instance, denoise_settings),
            SingleFramesOnly=False,
            ShellExecute=False,
            Shell="cmd",
        )
        return asdict(plugin_info)
```

- [ ] **Step 4: Update `process` to resolve, validate, and apply the backend**

In `process`, inside the second `try:` block, **after** the `assert self._deadline_url` line and **before** `job_info = self.get_generic_job_info(instance)`, add:

```python
            # Resolve the denoiser backend from settings and validate config.
            project_settings = context.data["project_settings"]
            denoise_settings = project_settings["luma-denoise"]
            backend_name = denoise_settings.get("denoiser", "renderman")
            self._backend = get_denoiser_backend(backend_name)
            self._backend.validate(instance, denoise_settings)
            instance.data["denoise_backend"] = self._backend.name
            self.log.info(f"Using denoiser backend: {self._backend.name}")
```

Then **replace** the env-var block (the lines fetching `denoise_pixar_lic`, `rmPATH`, `rmTREE` and setting `EnvironmentKeyValue` — including the duplicate `project_settings`/`denoise_settings` lookups directly above them) with:

```python
            # Backend-specific job environment (license, PATH, etc.).
            for key, value in self._backend.get_environment(
                    denoise_settings).items():
                self.job_info.EnvironmentKeyValue[key] = value
```

- [ ] **Step 5: Delete the methods that moved to the RenderMan backend**

Delete from the plugin: `_count_custom_frames`, `detectlargeimage`, `get_expected_resolution`, `iter_render_products` (all now live in `denoisers/renderman.py`). `get_denoise_enabled` and the rest of `process` stay.

- [ ] **Step 6: Verify**

Run: `python -m py_compile client/luma_denoise/plugins/publish/houdini/luma_denoise_publish.py`
Expected: exit 0.

Run: `git grep -n "denoise_rmantree_path\|denoise_pixar_lic\|detectlargeimage" -- client server`
Expected: no hits.

Run: `python -m pytest tests -v`
Expected: all PASSED.

- [ ] **Step 7: Commit**

```bash
git add client/luma_denoise/plugins/publish/houdini/luma_denoise_publish.py
git commit -m "refactor(publish): delegate denoise submission to denoiser backends"
```

---

### Task 10: Version bump, docs, package build

**Files:**
- Modify: `package.py`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Bump the version**

In `package.py` change:

```python
version = "0.1.4"
```

to:

```python
version = "0.2.0"
```

(`create_package.py` regenerates `client/luma_denoise/version.py` from this at build time — no other file to touch.)

- [ ] **Step 2: Update CLAUDE.md**

In `CLAUDE.md` (repo root), make these edits:

1. Replace the line `There is no test suite, linter, or CI pipeline. Python target is 3.9 (see `.python-version`).` with:

```markdown
Run tests with `python -m pytest tests -v` (no linter or CI pipeline). Python target is 3.9 (see `.python-version`).
```

2. In the "Publish Plugin Chain" table, replace the `LumaDenoiseUsdRender` row description `Submits Pixar denoise job to Deadline (depends on render job). Stores `denoise_job_id`` with:

```markdown
| Integrator +0.1 | `LumaDenoiseUsdRender` | Submits denoise job (backend from `denoiser` setting: RenderMan or OIDN) to Deadline (depends on render job). Stores `denoise_job_id` |
```

3. After the "### Deadline Job Dependency Chain" code block, add:

```markdown
### Denoiser Backends

The denoise step is abstracted behind `client/luma_denoise/denoisers/`
(`DenoiserBackend` strategy classes, selected by the `denoiser` project
setting). Each backend runs a standalone wrapper script from
`client/luma_denoise/scripts/` on the Deadline worker:

- `renderman` — `renderman_denoise.py` wraps Pixar `denoise_batch`
- `oidn` — `oidn_denoise.py` extracts beauty/albedo/normal via oiiotool and
  runs `oidnDenoise` (albedo + normal guide AOVs are REQUIRED in the render)

Both wrappers write a `<seq>.denoise.json` sidecar next to the denoised
frames; `oiio_combine.py` reads it for the beauty rename map (falling back
to the `beauty_rename_map_denoised` setting when absent). Wrapper scripts
deploy to a shared filesystem; per-backend `wrapper_script_path` settings
locate them ({version} token supported).
```

4. In "Key Data Flow Through instance.data", add the line:

```markdown
- `instance.data["denoise_backend"]` — "renderman" or "oidn", set by `LumaDenoiseUsdRender`
```

- [ ] **Step 3: Full verification**

Run: `python -m pytest tests -v`
Expected: all PASSED.

Run: `python create_package.py --skip-zip`
Expected: exits 0; the build output under `package/` contains `server/` and a client zip/folder whose `luma_denoise/` includes `denoisers/` and all three wrapper scripts in `scripts/`. The generated `client/luma_denoise/version.py` shows `0.2.0`.

- [ ] **Step 4: Commit**

```bash
git add package.py CLAUDE.md
git commit -m "build: bump version to 0.2.0, document denoiser backends"
```

---

## Post-Implementation (manual, outside this plan)

1. Build the zip (`python create_package.py`) and upload to the AYON server.
2. Re-enter the denoise settings in the server UI (new nested layout) and set
   both `wrapper_script_path` values.
3. Deploy `renderman_denoise.py` and `oidn_denoise.py` next to
   `oiio_combine.py` on the shared filesystem under the `0.2.0` folder.
4. Verify `oidnDenoise` is installed on the Deadline workers and that the
   Luma Render HDA emits albedo + normal AOVs before switching the dropdown
   to OIDN.
