# Spec: single Wrapper Scripts Directory (luma-denoise 0.6.0)

**Date:** 2026-06-17
**Status:** Approved
**Type:** Breaking settings change

## Problem

luma-denoise currently exposes three separate wrapper-script path settings,
each pointing at one file whose name is already hard-coded in the addon:

| Setting key | File | Consumer |
|---|---|---|
| `denoise.renderman.wrapper_script_path` | `renderman_denoise.py` | `denoisers/base.py:_resolve_wrapper_path` |
| `denoise.oidn.wrapper_script_path` | `oidn_denoise.py` | `denoisers/base.py:_resolve_wrapper_path` |
| `combine.wrapper_script_path` | `oiio_combine.py` | `plugins/publish/houdini/extract_oiio_combine.py` |

All three wrapper scripts deploy together to the same shared-filesystem folder
(per addon version). Asking the operator to enter three full file paths that
differ only by filename is redundant and error-prone: every version bump means
editing three fields, and the filenames are not the operator's to choose — they
are fixed by the addon.

## Goal

Replace the three per-file path fields with **one directory** setting. The addon
already knows each wrapper's filename, so it assembles `<directory>/<filename>`
itself.

## Design

### Settings (`server/settings.py`)

Remove `wrapper_script_path` from `RendermanDenoiserSettings`,
`OidnDenoiserSettings`, and `CombineSettings`.

Add one field to `SharedToolsSettings` (directly under `python_executable`):

```python
scripts_directory: str = SettingsField(
    "",
    title="Wrapper Scripts Directory",
    description=(
        "Directory containing the luma-denoise wrapper scripts "
        "(renderman_denoise.py, oidn_denoise.py, oiio_combine.py) on the "
        "shared library. Single value - Deadline Path Mapping translates it "
        "per worker OS. Supports the {version} token. MUST be set."
    ),
)
```

This is a **breaking** change: the three old keys disappear, so the operator
re-enters one directory once. Ships as **0.6.0**.

### Resolution helper (`denoisers/base.py`)

The directory + filename join lives in exactly one place — a new module-level
function so both the denoiser backends and the combine plugin share it:

```python
def resolve_wrapper_path(settings: dict, filename: str) -> str:
    """Join shared.scripts_directory with a wrapper filename.

    Reads the single scripts_directory setting (Deadline Path Mapping
    translates it per worker), substitutes {version}, and appends the
    fixed wrapper filename. Raises with an actionable message when unset.
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

`DenoiserBackend._resolve_wrapper_path` becomes a thin instance method that
delegates:

```python
def _resolve_wrapper_path(self, settings: dict) -> str:
    return resolve_wrapper_path(settings, self.wrapper_filename)
```

So `renderman.py` and `oidn.py` are unchanged below the surface — they still
call `self._resolve_wrapper_path(settings)`.

### Combine plugin (`plugins/publish/houdini/extract_oiio_combine.py`)

Add a module-level constant `WRAPPER_FILENAME = "oiio_combine.py"`. Replace the
current `combine.wrapper_script_path` read + raise + `{version}` substitution
block with:

```python
from luma_denoise.denoisers.base import DenoiserBackend, resolve_wrapper_path
...
wrapper_path = resolve_wrapper_path(oiio_settings, WRAPPER_FILENAME)
```

`oiio_settings` is `project_settings["luma-denoise"]`, which has the
`{denoise, combine, shared}` shape the helper expects.

### Out of scope / unchanged

- The arg-name contract between backends and wrapper scripts (the wrappers'
  argparse is untouched — this only changes argv[0], the wrapper path itself).
- Per-OS tool roots (`rmantree_path`, `oidn_root_path`, `oiio_root_path`) and
  their `--<tool>-root-<plat>` flags.
- `python_executable`, exe-name settings, rename maps, frame logic.
- The wrapper scripts in `client/luma_denoise/scripts/`.

## Tests

In `tests/test_denoiser_backends.py`:
- Move `wrapper_script_path` out of `RM_SETTINGS`/`OIDN_SETTINGS` into a top-level
  `shared.scripts_directory`.
- `test_base_resolve_wrapper_path_substitutes_version` → assert the joined path
  ends with `/renderman_denoise.py` and has no `{version}`.
- `test_base_resolve_wrapper_path_empty_raises` → match `scripts_directory`.
- `test_renderman_validate_requires_wrapper_path` /
  `test_oidn_validate_requires_wrapper_path` → match `scripts_directory`.
- Add `test_resolve_wrapper_path` covering the module function directly,
  including trailing-slash normalization (`L:/s/{version}/` → no doubled slash).

In `tests/test_oiio_combine.py`: update any reference to
`combine.wrapper_script_path` to `shared.scripts_directory`.

Expectation: full suite green (currently 94; net count may shift by 1-2 with
the added/removed assertions).

## Version & docs

- `package.py` → `version = "0.6.0"`.
- `CLAUDE.md`: update the `settings.py` bullet (one Scripts Directory under
  Shared Tools, not three per-script paths) and the wrapper-deploy note (all
  three scripts go in one folder).
