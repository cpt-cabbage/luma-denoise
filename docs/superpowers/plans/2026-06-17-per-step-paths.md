# Per-Step Single-Value Tool Paths Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Revert the per-OS/worker-side path model (0.4.0+0.5.0): RenderMan denoise runs `denoise_batch` directly (no Python); OIDN and combine keep Python wrappers but take single-value paths resolved at submit time for their own single-OS pool.

**Architecture:** Each step is configured with explicit single-value paths; the code does no OS detection, no per-OS dicts, no path mapping for tool installs. `denoisers/base.py` gains a `join_bin(root, exe)` helper and reads each backend's own `python_executable`. RenderMan returns the native `denoise_batch` as its executable; OIDN/combine pass resolved `--oidn-exe`/`--oiiotool` full paths to their wrappers.

**Tech Stack:** Python 3.9, Pydantic `ayon_server` settings, pytest. Houdini/`pxr` imports are call-time only.

---

### Task 1: base.py — `join_bin`, per-backend executable, drop platform helpers

**Files:**
- Modify: `client/luma_denoise/denoisers/base.py`
- Test: `tests/test_denoiser_backends.py`

- [ ] **Step 1: Write failing tests**

In `tests/test_denoiser_backends.py`, replace `test_base_get_executable_returns_python_executable` (lines 34-38) with:

```python
def test_base_get_executable_reads_backend_python():
    backend = base.DenoiserBackend()
    backend.name = "oidn"
    assert backend.get_executable(
        {"denoise": {"oidn": {"python_executable": "/usr/bin/python3"}}}) == "/usr/bin/python3"
    assert backend.get_executable({}) == "python"


def test_join_bin_joins_root_and_exe():
    assert base.join_bin("/opt/oidn", "oidnDenoise") == "/opt/oidn/bin/oidnDenoise"
    assert base.join_bin("/opt/oidn/", "oidnDenoise") == "/opt/oidn/bin/oidnDenoise"
    assert base.join_bin("C:/oiio", "oiiotool.exe") == "C:/oiio/bin/oiiotool.exe"
```

Delete `test_resolve_platform_value_dict_and_passthrough` (lines 57-63),
`test_platform_triplet_args_from_dict_omits_empty` (lines 66-72), and
`test_platform_triplet_args_from_plain_string` (lines 74-78).

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_denoiser_backends.py::test_join_bin_joins_root_and_exe -v`
Expected: FAIL — `module 'denoisers.base' has no attribute 'join_bin'`.

- [ ] **Step 3: Implement base.py changes**

In `client/luma_denoise/denoisers/base.py`:

Delete the `resolve_platform_value` function (lines 25-33) and the
`platform_triplet_args` staticmethod (lines 90-109).

Add this module-level function after `quote` (after line 22):

```python
def join_bin(root: str, exe: str) -> str:
    """Join an install root with bin/<exe>, used verbatim (no .exe magic)."""
    return f"{root.rstrip('/')}/bin/{exe}"
```

Replace the `get_executable` method body (lines 50-58) with:

```python
    def get_executable(self, settings: dict) -> str:
        """Executable for the Deadline job — the backend's worker Python.

        Single value for the backend's single-OS pool (resolved at submit).
        """
        return self._backend_settings(settings).get(
            "python_executable", "python") or "python"
```

(Keep `quote`, `resolve_wrapper_path`, `_resolve_wrapper_path`,
`_backend_settings`, `rename_pair_args`.)

- [ ] **Step 4: Run the base/helper tests**

Run: `python -m pytest tests/test_denoiser_backends.py -k "join_bin or get_executable or resolve_wrapper_path or quote or rename_pair" -v`
Expected: PASS. (Backend-specific tests still fail until Tasks 2-3.)

- [ ] **Step 5: Commit**

```bash
git add client/luma_denoise/denoisers/base.py tests/test_denoiser_backends.py
git commit -m "refactor(denoisers): add join_bin, per-backend executable, drop platform helpers"
```

---

### Task 2: RenderMan backend runs denoise_batch directly

**Files:**
- Modify: `client/luma_denoise/denoisers/renderman.py`
- Test: `tests/test_denoiser_backends.py`

- [ ] **Step 1: Replace RM_SETTINGS fixture and RenderMan tests**

In `tests/test_denoiser_backends.py`, replace the `RM_SETTINGS` dict (lines 84-104) with:

```python
RM_SETTINGS = {
    "denoise": {
        "denoiser": "renderman",
        "renderman": {
            "rmantree_path": "/opt/pixar/RenderManProServer-26.3",
            "denoise_exe": "denoise_batch",
            "pixar_license": "9010@x",
            "tiled_denoise_threshold": 2048,
            "beauty_rename_map": [
                {"source": "Ci.r", "target": "R"},
                {"source": "a.Z", "target": "A"},
            ],
        },
    },
}
```

Replace the RenderMan test block — `test_renderman_arguments_basic` (121-139),
`test_renderman_environment` (167-170), `test_renderman_validate_requires_wrapper_path`
(173-177) — with:

```python
def test_renderman_executable_is_native_denoise_batch():
    backend = RendermanDenoiser()
    assert backend.get_executable(RM_SETTINGS) == \
        "/opt/pixar/RenderManProServer-26.3/bin/denoise_batch"


def test_renderman_arguments_basic(monkeypatch):
    backend = _rm_backend(monkeypatch)
    args = backend.get_arguments(make_instance(), RM_SETTINGS)
    assert "-a 0" in args
    assert "--clean-alpha" in args
    assert "--progress" in args
    assert "-o /renders/shot/main/denoised" in args
    assert "/renders/shot/main/shot_main.1001.exr" in args
    assert "1001-1100" in args
    # 100 frames >= 8 -> cross-frame on; not a large image -> no tiles
    assert "-cf" in args
    assert "--tiles" not in args
    # no wrapper, no python, no rename flags
    assert "renderman_denoise.py" not in args
    assert "--rename" not in args


def test_renderman_environment():
    backend = RendermanDenoiser()
    env = backend.get_environment(RM_SETTINGS)
    assert env == {
        "RMANTREE": "/opt/pixar/RenderManProServer-26.3",
        "PIXAR_LICENSE_FILE": "9010@x",
    }


def test_renderman_validate_requires_rmantree():
    backend = RendermanDenoiser()
    bad = {"denoise": {"renderman": {"rmantree_path": ""}}}
    with pytest.raises(RuntimeError, match="rmantree_path"):
        backend.validate(make_instance(), bad)
```

In `test_renderman_arguments_large_image_enables_tiles` (161-164), keep it but
change the assertion to the native flag:

```python
def test_renderman_arguments_large_image_enables_tiles(monkeypatch):
    backend = _rm_backend(monkeypatch, large_image=True)
    args = backend.get_arguments(make_instance(), RM_SETTINGS)
    assert "--tiles 2 2" in args
```

Update `test_rename_pair_args_skips_incomplete_pairs` (282-289) — it builds its
own settings dict, no change needed there.

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_denoiser_backends.py::test_renderman_executable_is_native_denoise_batch -v`
Expected: FAIL (current `get_executable` returns python wrapper path logic).

- [ ] **Step 3: Rewrite renderman.py**

Replace the top of `client/luma_denoise/denoisers/renderman.py` — the import
(line 7) and the `get_arguments`/`get_environment`/`validate` methods
(lines 17-56) — with:

```python
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
```

(Keep the rest of the file unchanged: `_frame_count`, `_count_custom_frames`,
`detect_large_image`, `_iter_render_products`. The `import os` at line 5 stays.)

- [ ] **Step 4: Run RenderMan tests**

Run: `python -m pytest tests/test_denoiser_backends.py -k renderman -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add client/luma_denoise/denoisers/renderman.py tests/test_denoiser_backends.py
git commit -m "feat(renderman): run denoise_batch directly with single rmantree path"
```

---

### Task 3: OIDN backend passes resolved single-value paths

**Files:**
- Modify: `client/luma_denoise/denoisers/oidn.py`
- Test: `tests/test_denoiser_backends.py`

- [ ] **Step 1: Replace OIDN_SETTINGS fixture and OIDN tests**

In `tests/test_denoiser_backends.py`, replace `OIDN_SETTINGS` (lines 190-211) with:

```python
OIDN_SETTINGS = {
    "shared": {"scripts_directory": "L:/scripts/{version}"},
    "denoise": {
        "denoiser": "oidn",
        "oidn": {
            "python_executable": "/usr/bin/python3",
            "oidn_root_path": "/opt/oidn",
            "denoise_exe": "oidnDenoise",
            "oiio_root_path": "/opt/oiio",
            "oiio_exe": "oiiotool",
            "wrapper_script_path": "",
            "beauty_channel": "beauty",
            "albedo_channel": "albedo",
            "normal_channel": "N",
            "beauty_rename_map": [
                {"source": "beauty.r", "target": "R"},
                {"source": "a.Z", "target": "A"},
            ],
        },
    },
}
```

Replace `test_oidn_arguments` (228-246), `test_oidn_environment_returns_empty`
(249-252), and `test_oidn_validate_requires_wrapper_path` (255-259) with:

```python
def test_oidn_executable_is_backend_python():
    backend = OidnDenoiser()
    assert backend.get_executable(OIDN_SETTINGS) == "/usr/bin/python3"


def test_oidn_arguments():
    backend = OidnDenoiser()
    args = backend.get_arguments(make_instance(), OIDN_SETTINGS)
    assert args.startswith("L:/scripts/")
    assert "--oidn-exe /opt/oidn/bin/oidnDenoise" in args
    assert "--oiiotool /opt/oiio/bin/oiiotool" in args
    assert "--oidn-root" not in args
    assert "--oiio-root" not in args
    assert "--input /renders/shot/main/shot_main.1001.exr" in args
    assert "--output-dir /renders/shot/main/denoised" in args
    assert "--frame-start 1001" in args
    assert "--frame-end 1100" in args
    assert "--beauty-channel beauty" in args
    assert "--albedo-channel albedo" in args
    assert "--normal-channel N" in args
    assert "--rename beauty.r=R" in args
    assert "--rename a.Z=A" in args


def test_oidn_environment_returns_empty():
    backend = OidnDenoiser()
    assert backend.get_environment(OIDN_SETTINGS) == {}


def test_oidn_validate_requires_wrapper_path():
    backend = OidnDenoiser()
    bad = _oidn_settings_with()
    bad["shared"]["scripts_directory"] = ""
    with pytest.raises(RuntimeError, match="scripts_directory"):
        backend.validate(make_instance(), bad)


def test_oidn_validate_requires_oidn_root():
    backend = OidnDenoiser()
    bad = _oidn_settings_with(oidn_root_path="")
    with pytest.raises(RuntimeError, match="oidn_root_path"):
        backend.validate(make_instance(), bad)
```

(`test_oidn_validate_requires_guide_channels` at 262-266 stays.)

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_denoiser_backends.py::test_oidn_arguments -v`
Expected: FAIL (still emits `--oidn-root-*` per-OS flags).

- [ ] **Step 3: Rewrite oidn.py**

Replace the import (line 7) and `get_arguments`/`validate`
(lines 22-71) of `client/luma_denoise/denoisers/oidn.py` with:

```python
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
                    "project settings (OIDN group).")
```

(`from __future__ import annotations` and `import os` at the top stay.)

- [ ] **Step 4: Run the full backend test module**

Run: `python -m pytest tests/test_denoiser_backends.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add client/luma_denoise/denoisers/oidn.py tests/test_denoiser_backends.py
git commit -m "feat(oidn): pass single-value resolved tool paths to the wrapper"
```

---

### Task 4: Settings — per-step single values, shrink Shared Tools

**Files:**
- Modify: `server/settings.py`

(No unit test — `settings.py` imports `ayon_server`; verify via AST parse + the
build in Task 9.)

- [ ] **Step 1: RenderMan settings → single rmantree string**

In `RendermanDenoiserSettings`, replace the `rmantree_path` field (the
`MultiplatformPathModel` block, currently lines 40-51) with:

```python
    rmantree_path: str = SettingsField(
        "/opt/pixar/RenderManProServer-26.3",
        title="RenderMan Root Path (RMANTREE)",
        description=(
            "RMANTREE install root on the denoise pool. The denoise job runs "
            "<RMANTREE>/bin/<denoise_exe> directly (no Python). Single value "
            "for this step's single-OS pool."
        ),
    )
```

- [ ] **Step 2: OIDN settings → add python + single roots**

In `OidnDenoiserSettings`, replace the `oidn_root_path` field (the
`MultiplatformPathModel` block, currently lines 108-116) with the following
four fields (python first, then single-value roots/exe):

```python
    python_executable: str = SettingsField(
        "python",
        title="Python Executable (OIDN pool)",
        description=(
            "Python that Deadline launches for oidn_denoise.py on the OIDN "
            "pool. Single value for this step's single-OS pool."
        ),
    )

    oidn_root_path: str = SettingsField(
        "/opt/oidn",
        title="OIDN Root Path",
        description="OIDN install root on the OIDN pool. Single value.",
    )

    oiio_root_path: str = SettingsField(
        "/opt/oiio",
        title="OIIO Root Path (OIDN pool)",
        description=(
            "OpenImageIO install root on the OIDN pool, used to extract "
            "beauty/albedo/normal. Single value."
        ),
    )

    oiio_exe: str = SettingsField(
        "oiiotool",
        title="oiiotool Executable Name (OIDN pool)",
        description="oiiotool name in <OIIO root>/bin (e.g. oiiotool or oiiotool.exe).",
    )
```

(Keep `denoise_exe`, `beauty_channel`, `albedo_channel`, `normal_channel`,
`beauty_rename_map` in `OidnDenoiserSettings`. The OIDN wrapper script path
still comes from `shared.scripts_directory`.)

- [ ] **Step 3: Combine settings → add python + single oiio root/exe**

In `CombineSettings`, add these three fields immediately after the `group`
field (after line 259):

```python
    python_executable: str = SettingsField(
        "python",
        title="Python Executable (combine pool)",
        description=(
            "Python that Deadline launches for oiio_combine.py on the combine "
            "pool (Windows). Single value for this step's single-OS pool."
        ),
    )

    oiio_root_path: str = SettingsField(
        "C:/Program Files/OpenImageIO",
        title="OIIO Root Path (combine pool)",
        description="OpenImageIO install root on the combine pool. Single value.",
    )

    oiio_exe: str = SettingsField(
        "oiiotool.exe",
        title="oiiotool Executable Name (combine pool)",
        description="oiiotool name in <OIIO root>/bin (e.g. oiiotool.exe on Windows).",
    )
```

- [ ] **Step 4: Shrink SharedToolsSettings to scripts_directory only**

Replace the entire `SharedToolsSettings` class (currently lines 373-403) with:

```python
class SharedToolsSettings(BaseSettingsModel):
    """Tools shared across steps via the path-mapped library share."""

    scripts_directory: str = SettingsField(
        "",
        title="Wrapper Scripts Directory",
        description=(
            "Directory containing the luma-denoise wrapper scripts "
            "(oidn_denoise.py, oiio_combine.py) on the shared library. Single "
            "value - Deadline Path Mapping translates it per worker OS. "
            "Supports the {version} token. MUST be set when using OIDN or the "
            "OIIO combine step."
        ),
    )
```

- [ ] **Step 5: Remove MultiplatformPathModel**

Delete the `MultiplatformPathModel` class (currently lines 30-34) — no field
references it anymore.

- [ ] **Step 6: Verify the file parses**

Run: `python -c "import ast; ast.parse(open('server/settings.py').read()); print('OK')"`
Expected: `OK`.

- [ ] **Step 7: Commit**

```bash
git add server/settings.py
git commit -m "feat(settings)!: per-step single-value tool paths, shrink Shared Tools"
```

---

### Task 5: OIDN wrapper — drop per-OS resolution, require resolved exes

**Files:**
- Modify: `client/luma_denoise/scripts/oidn_denoise.py`
- Test: `tests/test_oidn_denoise.py`

- [ ] **Step 1: Delete the obsolete resolve_tools tests**

In `tests/test_oidn_denoise.py`, delete `test_resolve_tools_prefers_roots`
(lines 202-217) and `test_resolve_tools_missing_oidn_root_raises` (lines 220-232).

- [ ] **Step 2: Make --oidn-exe/--oiiotool required in parse_args**

In `client/luma_denoise/scripts/oidn_denoise.py`, in `parse_args`:
- Delete the `--oidn-root-windows/linux/darwin` arguments (lines 86-91).
- Delete the `--oidn-exe-name` argument (lines 92-95).
- Delete the `--oiio-root-windows/linux/darwin` arguments (lines 96-101).
- Delete the `--oiio-exe-name` argument (lines 102-105).
- Change `--oidn-exe` (lines 106-107) to required:

```python
    parser.add_argument("--oidn-exe", required=True, dest="oidn_exe",
                        help="Absolute path to oidnDenoise on the worker.")
```

- Change `--oiiotool` (lines 108-109) to required:

```python
    parser.add_argument("--oiiotool", required=True, dest="oiiotool",
                        help="Absolute path to oiiotool on the worker.")
```

- [ ] **Step 3: Remove platform helpers and rewrite _run env logic**

Delete `import platform as _platform_mod` (line 49), the `_SYSTEM` cache and its
comment (lines 60-62), `current_platform` (lines 68-71), `build_tool_path`
(lines 74-79), `_pick_root` (lines 141-144), and `resolve_tools`
(lines 146-167).

Replace the head of `_run` (lines 405-411) — the `resolve_tools` call and env
setup — with:

```python
    env = os.environ.copy()
    bin_dir = os.path.dirname(args.oidn_exe.replace("\\", "/"))
    if bin_dir:
        env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
```

(`args.oidn_exe`/`args.oiiotool` now come straight from argparse — no
reassignment needed. The rest of `_run` is unchanged.)

- [ ] **Step 4: Run the OIDN wrapper tests**

Run: `python -m pytest tests/test_oidn_denoise.py -v`
Expected: all PASS (the fixtures already pass `--oidn-exe`/`--oiiotool`).

- [ ] **Step 5: Commit**

```bash
git add client/luma_denoise/scripts/oidn_denoise.py tests/test_oidn_denoise.py
git commit -m "refactor(oidn_denoise): require resolved exe paths, drop per-OS resolution"
```

---

### Task 6: Combine wrapper — drop per-OS resolution, require --oiiotool

**Files:**
- Modify: `client/luma_denoise/scripts/oiio_combine.py`
- Test: `tests/test_oiio_combine.py`

- [ ] **Step 1: Delete the obsolete per-OS tests**

In `tests/test_oiio_combine.py`, delete `test_build_tool_path_windows_appends_exe`
(lines 646-649) and `test_parse_args_accepts_oiio_root_flags` (lines 652-668).
Also delete the `# --- worker-side resolution helpers ---` comment block header
(line 643) if present.

- [ ] **Step 2: Make --oiiotool required, drop per-OS flags in parse_args**

In `client/luma_denoise/scripts/oiio_combine.py`, in `parse_args`:
- Change `--oiiotool` (lines 70-71) to required:

```python
    parser.add_argument("--oiiotool", required=True,
                        help="Absolute path to oiiotool on the worker.")
```

- Delete `--oiio-root-windows/linux/darwin` (lines 73-78) and `--oiio-exe-name`
  (lines 79-80).

- [ ] **Step 3: Remove platform helpers and the _run resolution block**

Delete `import platform as _platform_mod` (top of file), the `_SYSTEM` cache and
comment (lines 40-42), `current_platform` (lines 45-48), and `build_tool_path`
(lines 51-56).

Replace the head of `_run` (lines 477-485) — the resolution block — with just
the pass-through computation:

```python
def _run(args: argparse.Namespace) -> int:
    pass_through = (args.denoised == args.raw)
```

(Everything below — the `if args.verbose:` block onward — is unchanged and uses
`args.oiiotool` directly.)

- [ ] **Step 4: Run the combine wrapper tests**

Run: `python -m pytest tests/test_oiio_combine.py -v`
Expected: all PASS (the fixtures already pass `--oiiotool`).

- [ ] **Step 5: Commit**

```bash
git add client/luma_denoise/scripts/oiio_combine.py tests/test_oiio_combine.py
git commit -m "refactor(oiio_combine): require --oiiotool, drop per-OS resolution"
```

---

### Task 7: Combine plugin — per-step python + resolved oiiotool

**Files:**
- Modify: `client/luma_denoise/plugins/publish/houdini/extract_oiio_combine.py`

- [ ] **Step 1: Update the import**

Change line 9 from:

```python
from luma_denoise.denoisers.base import DenoiserBackend, resolve_wrapper_path
```

to:

```python
from luma_denoise.denoisers.base import join_bin, resolve_wrapper_path
```

- [ ] **Step 2: Read per-step settings and emit resolved oiiotool**

In `get_plugin_info`, replace the settings reads and the oiio flag emission.
Replace these lines (currently 143-145):

```python
        oiio_root_value = shared_settings.get("oiio_root_path", "")
        oiio_exe_name = shared_settings.get("oiio_exe", "oiiotool") or "oiiotool"
        python_exe = shared_settings.get("python_executable", "python") or "python"
```

with (reading from `combine_settings`, not `shared_settings`):

```python
        oiiotool = join_bin(
            combine_settings.get("oiio_root_path", ""),
            combine_settings.get("oiio_exe", "oiiotool") or "oiiotool")
        python_exe = combine_settings.get("python_executable", "python") or "python"
```

Then replace the oiio-flag emission (currently lines 215-216):

```python
        parts.extend(DenoiserBackend.platform_triplet_args("oiio-root", oiio_root_value))
        parts.extend(["--oiio-exe-name", self._quote(oiio_exe_name)])
```

with:

```python
        parts.extend(["--oiiotool", self._quote(oiiotool)])
```

(The `shared_settings = oiio_settings.get("shared", {}) or {}` line at 141 can
stay — `resolve_wrapper_path` reads `shared.scripts_directory` from
`oiio_settings`. It is no longer used for oiio/python, which is harmless; remove
it if you prefer tidiness.)

- [ ] **Step 3: Verify the file parses**

Run: `python -c "import ast; ast.parse(open('client/luma_denoise/plugins/publish/houdini/extract_oiio_combine.py').read()); print('OK')"`
Expected: `OK`.

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest tests -q`
Expected: all PASS (no test imports the Houdini plugin).

- [ ] **Step 5: Commit**

```bash
git add client/luma_denoise/plugins/publish/houdini/extract_oiio_combine.py
git commit -m "feat(combine): per-step python + resolved oiiotool path"
```

---

### Task 8: Delete the dead RenderMan wrapper

**Files:**
- Delete: `client/luma_denoise/scripts/renderman_denoise.py`
- Delete: `tests/test_renderman_denoise.py`

- [ ] **Step 1: Remove the files**

```bash
git rm client/luma_denoise/scripts/renderman_denoise.py tests/test_renderman_denoise.py
```

- [ ] **Step 2: Confirm nothing references them**

Run: `python -m pytest tests -q`
Expected: all PASS (RenderMan now runs `denoise_batch` directly; no wrapper).

Also grep to be sure:
Run: `grep -rn "renderman_denoise" client/ server/ tests/ || echo "no refs"`
Expected: `no refs` (docs may still reference it; that's fine).

- [ ] **Step 3: Commit**

```bash
git commit -m "chore: remove dead renderman_denoise.py wrapper and its tests"
```

---

### Task 9: Version bump + docs

**Files:**
- Modify: `package.py`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Bump the version**

In `package.py`, change `version = "0.6.0"` to `version = "0.7.0"`. Also update
the `CLAUDE.md` "Version Management" line `version = "0.6.0"` to
`version = "0.7.0"`.

- [ ] **Step 2: Update CLAUDE.md architecture notes**

In `CLAUDE.md`:
- In "Server Side", update the `settings.py` bullet to: tool paths are per-step
  single values for each step's single-OS pool (RenderMan `rmantree_path`; OIDN
  `python_executable`/`oidn_root_path`/`oiio_root_path`; combine
  `python_executable`/`oiio_root_path`); Shared Tools holds only
  `scripts_directory`. No per-OS dicts or worker-side resolution.
- In "Denoiser Backends", update to: RenderMan runs `denoise_batch` directly (no
  Python wrapper; `renderman_denoise.py` removed); OIDN runs `oidn_denoise.py`
  via its pool's Python with resolved `--oidn-exe`/`--oiiotool`. Remove the
  sentence describing both wrappers writing sidecars (only OIDN does now).

- [ ] **Step 3: Build to confirm version + packaging**

Run: `python create_package.py --skip-zip`
Expected: exit 0; log line `Client 'version.py' updated to '0.7.0'`.

- [ ] **Step 4: Final full suite**

Run: `python -m pytest tests -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add package.py CLAUDE.md
git commit -m "build: bump version to 0.7.0, document per-step single-value paths"
```

---

## Self-Review

- **Spec coverage:** settings restructure (Task 4), `join_bin` + per-backend
  executable + drop platform helpers (Task 1), RenderMan direct exec (Task 2),
  OIDN resolved paths (Task 3), wrapper strips (Tasks 5-6), combine plugin
  (Task 7), delete RenderMan wrapper + tests (Task 8), version/docs (Task 9).
  Every spec section maps to a task.
- **Placeholder scan:** none; each code step shows full content or names exact
  lines/symbols to delete.
- **Type consistency:** `join_bin(root, exe)` defined in Task 1 and called with
  the same signature in Tasks 2, 3, 7. `get_executable` reads
  `python_executable` from backend settings (Task 1) — RenderMan overrides it
  (Task 2), OIDN inherits (Task 3), combine reads `combine.python_executable`
  directly (Task 7). `scripts_directory` key consistent across settings (Task 4)
  and `resolve_wrapper_path` (unchanged). Wrapper flags `--oidn-exe`/`--oiiotool`
  required (Tasks 5-6) match what the backends emit (Task 3) and the plugin
  emits (Task 7).
