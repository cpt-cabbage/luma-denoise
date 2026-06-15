# Per-Platform Paths Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** All tool/wrapper paths and exe names become windows/linux/darwin triplets, resolved at submission by a per-step Worker Platform dropdown (Denoising default linux, Combine default windows). Version 0.4.0.

**Spec:** `docs/superpowers/specs/2026-06-12-platform-paths-design.md`

**Files:** `server/settings.py`; `client/luma_denoise/denoisers/base.py`, `renderman.py`, `oidn.py`; `client/luma_denoise/plugins/publish/houdini/extract_oiio_combine.py`; `tests/test_denoiser_backends.py`; `package.py`; `CLAUDE.md`. Nothing else (wrappers, oiio_combine.py, luma_denoise_publish.py untouched).

Suite currently: 83 passed.

---

### Task 1: settings.py — MultiplatformPathModel + worker_platform enums

Add after `_denoiser_enum`:

```python
def _platform_enum():
    return [
        {"value": "windows", "label": "Windows"},
        {"value": "linux", "label": "Linux"},
        {"value": "darwin", "label": "macOS"},
    ]


class MultiplatformPathModel(BaseSettingsModel):
    """One value per worker platform."""
    _layout = "expanded"
    windows: str = SettingsField("", title="Windows")
    linux: str = SettingsField("", title="Linux")
    darwin: str = SettingsField("", title="macOS")
```

Convert the ten fields per the spec's table, e.g.:

```python
    rmantree_path: MultiplatformPathModel = SettingsField(
        default_factory=lambda: MultiplatformPathModel(
            windows="C:/Program Files/Pixar/RenderManProServer-26.3",
            linux="/opt/pixar/RenderManProServer-26.3",
            darwin="/Applications/Pixar/RenderManProServer-26.3",
        ),
        title="RenderMan Root Path",
        description="Path to RMANTREE, per worker platform.",
    )
```

(Same pattern for the other nine; wrapper paths default to all-empty
`default_factory=MultiplatformPathModel`; descriptions keep their existing
text plus "per worker platform" where it clarifies.)

Add `worker_platform` to `DenoiseSettings` after `group`:

```python
    worker_platform: str = SettingsField(
        "linux",
        title="Worker Platform",
        description=(
            "Platform of the Deadline workers that run the denoise job. "
            "All denoise paths below resolve for this platform at "
            "submission time."
        ),
        enum_resolver=_platform_enum,
    )
```

and to `CombineSettings` after `group` (default `"windows"`, description
referencing the combine job).

Verify: `python -m py_compile server/settings.py`; suite still 83.
Commit: `feat(settings)!: per-platform paths with per-step worker platform`

---

### Task 2: Backends — platform resolution (TDD)

**tests/test_denoiser_backends.py** — update fixtures: in `RM_SETTINGS`
set `"worker_platform": "linux"` inside `"denoise"`, and convert:

```python
            "rmantree_path": {"windows": "C:/Pixar/RMP", "linux": "/opt/pixar/RenderManProServer-26.3", "darwin": ""},
            "denoise_exe": {"windows": "denoise_batch.exe", "linux": "denoise_batch", "darwin": "denoise_batch"},
            "wrapper_script_path": {"windows": "W:/scripts/{version}/renderman_denoise.py", "linux": "L:/scripts/{version}/renderman_denoise.py", "darwin": ""},
```

In `OIDN_SETTINGS` set `"worker_platform": "linux"` and convert
`oidn_root_path` ({"linux": "/opt/oidn", "windows": "", "darwin": ""}),
`denoise_exe` ({"windows": "oidnDenoise.exe", "linux": "oidnDenoise",
"darwin": "oidnDenoise"}), `wrapper_script_path` ({"linux":
"L:/scripts/{version}/oidn_denoise.py", "windows": "", "darwin": ""}).
In BOTH fixtures convert `shared` to:

```python
    "shared": {
        "python_executable": {"windows": "python.exe", "linux": "/usr/bin/python3", "darwin": "python3"},
        "oiio_root_path": {"windows": "C:/oiio", "linux": "/opt/oiio", "darwin": ""},
        "oiio_exe": {"windows": "oiiotool.exe", "linux": "oiiotool", "darwin": "oiiotool"},
    },
```

Existing assertions (linux values + worker_platform=linux) keep passing
unchanged. Update the two base wrapper-path tests' settings to the dict
shape (`{"denoise": {"worker_platform": "linux", "renderman":
{"wrapper_script_path": {"linux": "L:/scripts/{version}/renderman_denoise.py",
"windows": "", "darwin": ""}}}}` etc.) and update
`test_base_get_executable_uses_python_executable_setting` to:

```python
def test_base_get_executable_resolves_worker_platform():
    backend = base.DenoiserBackend()
    settings = {
        "denoise": {"worker_platform": "windows"},
        "shared": {"python_executable": {
            "windows": "py.exe", "linux": "/usr/bin/python3", "darwin": ""}},
    }
    assert backend.get_executable(settings) == "py.exe"
    settings["denoise"]["worker_platform"] = "linux"
    assert backend.get_executable(settings) == "/usr/bin/python3"
    assert backend.get_executable({}) == "python"
```

Add new tests:

```python
def test_resolve_platform_value_dict_and_passthrough():
    assert base.resolve_platform_value(
        {"windows": "a.exe", "linux": "a", "darwin": ""}, "windows") == "a.exe"
    assert base.resolve_platform_value(
        {"windows": "a.exe", "linux": "a", "darwin": ""}, "darwin") == ""
    assert base.resolve_platform_value("plain", "linux") == "plain"
    assert base.resolve_platform_value(None, "linux") == ""


def test_renderman_arguments_windows_worker_platform(monkeypatch):
    import copy
    backend = _rm_backend(monkeypatch)
    settings = copy.deepcopy(RM_SETTINGS)
    settings["denoise"]["worker_platform"] = "windows"
    args = backend.get_arguments(make_instance(), settings)
    assert "--denoise-exe C:/Pixar/RMP/bin/denoise_batch.exe" in args
    assert args.startswith("W:/scripts/")


def test_wrapper_path_missing_for_platform_names_platform():
    backend = RendermanDenoiser()
    settings = {"denoise": {"worker_platform": "darwin", "renderman": {
        "wrapper_script_path": {"windows": "w", "linux": "l", "darwin": ""}}}}
    with pytest.raises(RuntimeError, match="darwin"):
        backend.validate(make_instance(), settings)
```

**base.py** — add module function after `quote`:

```python
def resolve_platform_value(value, worker_platform: str) -> str:
    """Resolve a multiplatform settings value for a worker platform.

    Accepts the {windows, linux, darwin} dict shape (AYON multiplatform
    path); plain strings pass through so pre-0.4.0 values keep working.
    """
    if isinstance(value, dict):
        return value.get(worker_platform, "") or ""
    return value or ""
```

In `DenoiserBackend` add:

```python
    def _worker_platform(self, settings: dict) -> str:
        denoise_settings = settings.get("denoise", {}) or {}
        return denoise_settings.get("worker_platform", "linux")
```

`get_executable` becomes:

```python
        shared = settings.get("shared", {}) or {}
        return resolve_platform_value(
            shared.get("python_executable", "python"),
            self._worker_platform(settings)) or "python"
```

`_resolve_wrapper_path` becomes:

```python
    def _resolve_wrapper_path(self, settings: dict) -> str:
        platform_key = self._worker_platform(settings)
        template = resolve_platform_value(
            self._backend_settings(settings).get("wrapper_script_path", ""),
            platform_key)
        if not template:
            raise RuntimeError(
                f"luma-denoise: 'denoise.{self.name}.wrapper_script_path' "
                f"has no value for worker platform '{platform_key}'. "
                f"Set it in the luma-denoise project settings to the "
                f"absolute path of {self.wrapper_filename or 'the wrapper script'} "
                "on a shared filesystem accessible from all render nodes. "
                "Use the {version} token for per-version paths."
            )
        return template.replace("{version}", ADDON_VERSION)
```

**renderman.py** — in `get_arguments`:

```python
        platform_key = self._worker_platform(settings)
        rman_root = resolve_platform_value(
            rm_settings.get("rmantree_path", ""), platform_key
        ) or "/opt/pixar/RenderManProServer-26.3"
        exe_name = resolve_platform_value(
            rm_settings.get("denoise_exe", ""), platform_key) or "denoise_batch"
```

(import `resolve_platform_value` from `.base`); in `get_environment` the
same `rman_root` resolution (platform via `self._worker_platform(settings)`).

**oidn.py** — same pattern for `oidn_root_path` (fallback "/opt/oidn"),
`denoise_exe` (fallback "oidnDenoise"), and the shared reads:

```python
        oiio_root = resolve_platform_value(
            shared.get("oiio_root_path", ""), platform_key) or "/opt/oiio"
        oiio_exe = resolve_platform_value(
            shared.get("oiio_exe", ""), platform_key) or "oiiotool"
```

and in `get_environment` resolve `oidn_root_path` the same way.

Verify: backends file all pass; full suite 86 passed.
Commit: `feat(denoisers): resolve paths per worker platform`

---

### Task 3: ExtractOiioCombine — combine worker platform

In `client/luma_denoise/plugins/publish/houdini/extract_oiio_combine.py`:

- Add import: `from luma_denoise.denoisers.base import resolve_platform_value`
- In `get_plugin_info` after deriving `combine_settings`/`shared_settings`:

```python
        worker_platform = combine_settings.get("worker_platform", "windows")
```

- Resolve the four reads (keep existing hardcoded fallbacks):
  - `oiio_root = resolve_platform_value(shared_settings.get("oiio_root_path", ""), worker_platform) or <existing long default>`
  - oiiotool exe: `resolve_platform_value(shared_settings.get("oiio_exe", ""), worker_platform) or "oiiotool"`
  - `python_exe = resolve_platform_value(shared_settings.get("python_executable", ""), worker_platform) or "python"`
  - `wrapper_template = resolve_platform_value(combine_settings.get("wrapper_script_path", ""), worker_platform)`; extend the RuntimeError message to include `... for worker platform '{worker_platform}'` (pre-compute the platform into the message; keep mentioning 'combine.wrapper_script_path').

Verify: `python -m py_compile` the file; full suite 86;
`git grep -n "oiiotool.exe" -- client/luma_denoise/plugins` → no hits.
Commit: `refactor(publish): combine resolves paths for its worker platform`

---

### Task 4: Version 0.4.0 + CLAUDE.md

- `package.py`: 0.3.0 → 0.4.0.
- `CLAUDE.md`: in the settings.py bullet, append: `All tool/wrapper paths
  are per-platform (windows/linux/darwin) and resolve via each step's
  Worker Platform dropdown at submission time.`
- Full verify: suite 86; `python create_package.py --skip-zip` exit 0.
- Commit: `build: bump version to 0.4.0, document per-platform paths`
  (include regenerated `client/luma_denoise/version.py`).
