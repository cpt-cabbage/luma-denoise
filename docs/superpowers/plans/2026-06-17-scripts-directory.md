# Single Wrapper Scripts Directory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the three per-script `wrapper_script_path` settings with one `shared.scripts_directory` directory setting; the addon joins `<directory>/<filename>` using each wrapper's known filename.

**Architecture:** A single module-level `resolve_wrapper_path(settings, filename)` in `denoisers/base.py` owns the directory+filename join. The denoiser backends call it via the existing `_resolve_wrapper_path` instance method (passing `self.wrapper_filename`); the combine plugin calls it directly with a fixed `oiio_combine.py` filename. Server settings drop three fields and gain one. Breaking change → 0.6.0.

**Tech Stack:** Python 3.9, Pydantic settings (`ayon_server`), pytest. Houdini/Deadline imports are call-time only.

---

### Task 1: `resolve_wrapper_path` helper in base.py (the join contract)

**Files:**
- Modify: `client/luma_denoise/denoisers/base.py`
- Test: `tests/test_denoiser_backends.py`

- [ ] **Step 1: Write the failing test for the new module function**

Add to `tests/test_denoiser_backends.py`, after `test_base_resolve_wrapper_path_empty_raises` (around line 55):

```python
def test_resolve_wrapper_path_joins_dir_and_filename():
    out = base.resolve_wrapper_path(
        {"shared": {"scripts_directory": "L:/s/{version}"}},
        "renderman_denoise.py")
    assert "{version}" not in out
    assert out.endswith("/renderman_denoise.py")


def test_resolve_wrapper_path_strips_trailing_slash():
    out = base.resolve_wrapper_path(
        {"shared": {"scripts_directory": "L:/s/"}}, "oiio_combine.py")
    assert out == "L:/s/oiio_combine.py"
    out_bs = base.resolve_wrapper_path(
        {"shared": {"scripts_directory": "L:/s\\"}}, "oiio_combine.py")
    assert out_bs == "L:/s/oiio_combine.py"


def test_resolve_wrapper_path_empty_raises():
    with pytest.raises(RuntimeError, match="scripts_directory"):
        base.resolve_wrapper_path({"shared": {"scripts_directory": ""}},
                                  "renderman_denoise.py")
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `python -m pytest tests/test_denoiser_backends.py::test_resolve_wrapper_path_joins_dir_and_filename -v`
Expected: FAIL with `AttributeError: module 'denoisers.base' has no attribute 'resolve_wrapper_path'`.

- [ ] **Step 3: Add the module-level function and rewire the instance method**

In `client/luma_denoise/denoisers/base.py`, add this module-level function right after the `resolve_platform_value` function (after line 33):

```python
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
```

Then replace the body of `DenoiserBackend._resolve_wrapper_path` (currently lines 78-88) with a delegating call:

```python
    def _resolve_wrapper_path(self, settings: dict) -> str:
        return resolve_wrapper_path(settings, self.wrapper_filename)
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `python -m pytest tests/test_denoiser_backends.py -k resolve_wrapper_path -v`
Expected: the 3 new tests PASS. The two OLD tests
(`test_base_resolve_wrapper_path_substitutes_version`,
`test_base_resolve_wrapper_path_empty_raises`) will FAIL — they still pass
`denoise.<name>.wrapper_script_path`. They are fixed in Task 2.

- [ ] **Step 5: Commit**

```bash
git add client/luma_denoise/denoisers/base.py tests/test_denoiser_backends.py
git commit -m "feat(denoisers): add resolve_wrapper_path joining shared.scripts_directory"
```

---

### Task 2: Point existing backend tests at scripts_directory

**Files:**
- Modify: `tests/test_denoiser_backends.py`

- [ ] **Step 1: Update the two stale base-helper tests**

Replace `test_base_resolve_wrapper_path_substitutes_version` (lines 41-47) and
`test_base_resolve_wrapper_path_empty_raises` (lines 50-54) with versions that
drive the directory setting through the instance method:

```python
def test_base_resolve_wrapper_path_substitutes_version():
    backend = base.DenoiserBackend()
    backend.name = "renderman"
    backend.wrapper_filename = "renderman_denoise.py"
    out = backend._resolve_wrapper_path(
        {"shared": {"scripts_directory": "L:/s/{version}"}})
    assert "{version}" not in out and out.endswith("/renderman_denoise.py")


def test_base_resolve_wrapper_path_empty_raises():
    backend = base.DenoiserBackend()
    backend.name = "renderman"
    backend.wrapper_filename = "renderman_denoise.py"
    with pytest.raises(RuntimeError, match="scripts_directory"):
        backend._resolve_wrapper_path({"shared": {"scripts_directory": ""}})
```

- [ ] **Step 2: Move wrapper path into shared for RM_SETTINGS**

In `RM_SETTINGS` (lines 84-104): add `"scripts_directory": "L:/scripts/{version}"`
to the `"shared"` dict, and DELETE the line
`"wrapper_script_path": "L:/scripts/{version}/renderman_denoise.py",` from the
`renderman` dict. Resulting `shared` block:

```python
    "shared": {
        "python_executable": "/usr/bin/python3",
        "scripts_directory": "L:/scripts/{version}",
        "oiio_root_path": {"windows": "C:/oiio", "linux": "/opt/oiio", "darwin": ""},
        "oiio_exe": "oiiotool",
    },
```

- [ ] **Step 3: Move wrapper path into shared for OIDN_SETTINGS**

In `OIDN_SETTINGS` (lines 190-211): add `"scripts_directory": "L:/scripts/{version}"`
to its `"shared"` dict, and DELETE the line
`"wrapper_script_path": "L:/scripts/{version}/oidn_denoise.py",` from the `oidn`
dict.

- [ ] **Step 4: Fix the validate error-match tests and the empty-wrapper-path checks**

`test_renderman_validate_requires_wrapper_path` (lines 173-177) — change the bad
settings and match string:

```python
def test_renderman_validate_requires_wrapper_path():
    backend = RendermanDenoiser()
    bad = {"shared": {"scripts_directory": ""}}
    with pytest.raises(RuntimeError, match="scripts_directory"):
        backend.validate(make_instance(), bad)
```

`test_oidn_validate_requires_wrapper_path` (lines 255-259) — the OIDN backend
validates guide channels too, so keep valid channels and only blank the
directory:

```python
def test_oidn_validate_requires_wrapper_path():
    backend = OidnDenoiser()
    bad = _oidn_settings_with()
    bad["shared"]["scripts_directory"] = ""
    with pytest.raises(RuntimeError, match="scripts_directory"):
        backend.validate(make_instance(), bad)
```

(`_oidn_settings_with` deep-copies `OIDN_SETTINGS`, which now carries a populated
`shared.scripts_directory` from Step 3, so blanking it here is the only change.)

- [ ] **Step 5: Run the full backend test module**

Run: `python -m pytest tests/test_denoiser_backends.py -v`
Expected: all PASS (the `get_arguments` tests already assert `args.startswith("L:/scripts/")` and still hold because the joined path is `L:/scripts/<version>/renderman_denoise.py`).

- [ ] **Step 6: Commit**

```bash
git add tests/test_denoiser_backends.py
git commit -m "test(denoisers): drive wrapper path via shared.scripts_directory"
```

---

### Task 3: Settings — drop three fields, add scripts_directory

**Files:**
- Modify: `server/settings.py`

(No unit test: `settings.py` imports `ayon_server`, which is not installed
locally. Verify by AST parse + the create_package build in Task 5.)

- [ ] **Step 1: Add scripts_directory to SharedToolsSettings**

In `server/settings.py`, in `SharedToolsSettings`, add this field immediately
after the `python_executable` field (after its closing `)` ~line 384):

```python
    scripts_directory: str = SettingsField(
        "",
        title="Wrapper Scripts Directory",
        description=(
            "Directory containing the luma-denoise wrapper scripts "
            "(renderman_denoise.py, oidn_denoise.py, oiio_combine.py) on the "
            "shared library. Single value - Deadline Path Mapping translates "
            "it per worker OS. Supports the {version} token. MUST be set."
        ),
    )
```

- [ ] **Step 2: Delete the RenderMan wrapper_script_path field**

In `RendermanDenoiserSettings`, delete the entire `wrapper_script_path` field
block (currently lines 78-86, the `SettingsField` titled
"RenderMan Wrapper Script Path (renderman_denoise.py)").

- [ ] **Step 3: Delete the OIDN wrapper_script_path field**

In `OidnDenoiserSettings`, delete the entire `wrapper_script_path` field block
(currently lines 127-135, titled "OIDN Wrapper Script Path (oidn_denoise.py)").

- [ ] **Step 4: Delete the Combine wrapper_script_path field**

In `CombineSettings`, delete the entire `wrapper_script_path` field block
(currently lines 261-269, titled "Combine Wrapper Script Path (oiio_combine.py)").

- [ ] **Step 5: Verify the file still parses**

Run: `python -c "import ast; ast.parse(open('server/settings.py').read()); print('OK')"`
Expected: `OK`.

- [ ] **Step 6: Commit**

```bash
git add server/settings.py
git commit -m "feat(settings)!: replace three wrapper paths with shared.scripts_directory"
```

---

### Task 4: Combine plugin uses resolve_wrapper_path

**Files:**
- Modify: `client/luma_denoise/plugins/publish/houdini/extract_oiio_combine.py`

- [ ] **Step 1: Import the helper and add the filename constant**

Change the import on line 9 from:

```python
from luma_denoise.denoisers.base import DenoiserBackend
```

to:

```python
from luma_denoise.denoisers.base import DenoiserBackend, resolve_wrapper_path
```

Add a constant near the other module-level defaults (after `DEFAULT_RENAME_RAW`,
~line 33):

```python
# Wrapper filename is fixed by the addon; the directory comes from settings.
WRAPPER_FILENAME = "oiio_combine.py"
```

- [ ] **Step 2: Replace the wrapper-path resolution block**

In `get_plugin_info`, replace the current block (lines 147-155):

```python
        wrapper_template = combine_settings.get("wrapper_script_path", "")
        if not wrapper_template:
            raise RuntimeError(
                "luma-denoise: 'combine.wrapper_script_path' is not set. "
                "Point it at oiio_combine.py on the shared library (Deadline "
                "Path Mapping translates it per worker). Use the {version} "
                "token for per-version paths."
            )
        wrapper_path = wrapper_template.replace("{version}", _ADDON_VERSION)
```

with:

```python
        wrapper_path = resolve_wrapper_path(oiio_settings, WRAPPER_FILENAME)
```

(`oiio_settings` is `project_settings["luma-denoise"]`, already in scope on
line 139, and has the `shared` key the helper reads. `_ADDON_VERSION` stays
imported for nothing else — leave the existing import; it is harmless and the
helper uses `ADDON_VERSION` from base.)

- [ ] **Step 3: Verify the file parses**

Run: `python -c "import ast; ast.parse(open('client/luma_denoise/plugins/publish/houdini/extract_oiio_combine.py').read()); print('OK')"`
Expected: `OK`.

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest tests -q`
Expected: all PASS (95+; no test imports the Houdini plugin).

- [ ] **Step 5: Commit**

```bash
git add client/luma_denoise/plugins/publish/houdini/extract_oiio_combine.py
git commit -m "feat(combine): resolve oiio_combine.py from shared.scripts_directory"
```

---

### Task 5: Version bump + docs

**Files:**
- Modify: `package.py`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Bump the version**

In `package.py`, change `version = "0.5.0"` to `version = "0.6.0"`.

- [ ] **Step 2: Update CLAUDE.md settings description**

In `CLAUDE.md`, in the "Server Side" section, the `settings.py` bullet currently
reads (in part): "wrapper/python/library paths are single values handled by
Deadline Path Mapping." Update it to note that the three wrapper scripts now
share one **Wrapper Scripts Directory** setting under Shared Tools (the addon
appends each fixed wrapper filename), while python/library paths remain single
Path-Mapped values.

In the "Denoiser Backends" section, update the sentence "Wrapper scripts deploy
to a shared filesystem; per-backend `wrapper_script_path` settings locate them
({version} token supported)." to: "Wrapper scripts deploy to a single shared
folder named by the `shared.scripts_directory` setting ({version} token
supported); the addon appends each wrapper's fixed filename."

- [ ] **Step 3: Build to confirm version + packaging**

Run: `python create_package.py --skip-zip`
Expected: exit 0; log line `Client 'version.py' updated to '0.6.0'`.

- [ ] **Step 4: Final full suite**

Run: `python -m pytest tests -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add package.py CLAUDE.md
git commit -m "build: bump version to 0.6.0, document single scripts directory"
```

---

## Self-Review

- **Spec coverage:** settings field swap (Task 3), single resolve helper (Task 1),
  backend rewire (Task 1), combine plugin (Task 4), test updates (Tasks 1-2),
  version+docs (Task 5). `test_oiio_combine.py` confirmed to have no
  `wrapper_script_path` reference, so no change there — matches spec.
- **Placeholder scan:** none; every code step shows full content.
- **Type consistency:** `resolve_wrapper_path(settings, filename)` signature used
  identically in base instance method (Task 1) and combine plugin (Task 4);
  `WRAPPER_FILENAME = "oiio_combine.py"` matches the deployed script name;
  `scripts_directory` key spelled identically across settings, helper, and tests.
