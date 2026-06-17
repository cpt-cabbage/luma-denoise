# Spec: per-step single-value tool paths (luma-denoise 0.7.0)

**Date:** 2026-06-17
**Status:** Approved
**Type:** Breaking settings + behavior change (reverts the 0.4.0 + 0.5.0 path model)

## Problem

The 0.4.0/0.5.0 path model assumed a *mixed-OS* Deadline pool and pushed
per-OS `{windows,linux,darwin}` resolution onto the worker. In reality each
step runs on its own *single-OS* pool (denoise on a Linux GPU pool, combine on
a Windows pool). That model also forced every step through a **Python wrapper**
so the wrapper could resolve tools at runtime — which introduced a hard
dependency on a correct Python interpreter being launchable on each pool.

This broke RenderMan denoise: before the 0.2.0 rewrite it ran `denoise_batch`
**directly** (no Python). After the rewrite, Deadline launches
`python renderman_denoise.py …`, and `shared.python_executable` (a Windows
path) does not exist on the Linux denoise pool → "Executable does not exist".
OIDN has the same Python-bootstrap exposure (its wrapper is also launched by
Python), and `shared.python_executable` / `shared.oiio_root_path` are *shared*
between the OIDN denoise step and the combine step even though those steps run
on different-OS pools.

## Goal

Lock the OS assumptions down in configuration, not in code. Each step is
configured with **explicit single-value paths** for the one OS pool it is
assigned to, resolved at submit time. The code performs **no** OS detection,
no per-OS dicts, no path-mapping reliance for tool installs.

- **RenderMan denoise** runs `denoise_batch` directly again (no Python).
- **OIDN denoise** keeps its Python wrapper (it must orchestrate oiiotool +
  `oidnDenoise`), but with single-value paths resolved at submit and passed to
  the wrapper as already-resolved executables.
- **OIIO combine** keeps its Python wrapper, single-value paths for the Windows
  pool.

## Design

### Settings (`server/settings.py`) — per-step single values

`MultiplatformPathModel` is removed. All tool paths become plain `str`.

**`RendermanDenoiserSettings`** (no Python, no wrapper):
- `rmantree_path: str` — RMANTREE root for the denoise pool (e.g.
  `/opt/pixar/RenderManProServer-26.3`).
- `denoise_exe: str = "denoise_batch"` — joined verbatim as
  `<rmantree_path>/bin/<denoise_exe>` (no `.exe` auto-append).
- `pixar_license: str`, `tiled_denoise_threshold: int`, `beauty_rename_map`.

**`OidnDenoiserSettings`** (Python wrapper):
- `python_executable: str = "python"` — the OIDN pool's Python (NEW, per-step).
- `oidn_root_path: str` — single value.
- `denoise_exe: str = "oidnDenoise"`.
- `oiio_root_path: str` — single value (OIDN pool's OIIO; NEW here).
- `oiio_exe: str = "oiiotool"` (NEW here).
- `beauty_channel`, `albedo_channel`, `normal_channel`, `beauty_rename_map`.

**`CombineSettings`** (Python wrapper):
- `python_executable: str = "python"` — combine pool's Python (NEW, per-step).
- `oiio_root_path: str` — single value (combine pool's OIIO; NEW here).
- `oiio_exe: str = "oiiotool"` (NEW here).
- existing combine options unchanged.

**`SharedToolsSettings`** shrinks to a single field:
- `scripts_directory: str` — folder on the path-mapped library share holding
  `oidn_denoise.py` + `oiio_combine.py` ({version} token supported). This is
  genuinely cross-OS (network share, Deadline Path Mapping handles it).
- REMOVE `python_executable` and `oiio_root_path`/`oiio_exe` from Shared (now
  per-step).

### Path joining helper (`denoisers/base.py`)

Add one helper, no OS logic:

```python
def join_bin(root: str, exe: str) -> str:
    """Join an install root with bin/<exe>, used verbatim (no .exe magic)."""
    return f"{root.rstrip('/')}/bin/{exe}"
```

`base.DenoiserBackend.get_executable` reads the backend's own
`python_executable` (`self._backend_settings(settings).get("python_executable",
"python")`). REMOVE `resolve_platform_value` and `platform_triplet_args`.
`resolve_wrapper_path` / `_resolve_wrapper_path` stay (OIDN + combine use
`shared.scripts_directory`).

### RenderMan backend (`denoisers/renderman.py`)

- `get_executable(settings)` → `join_bin(rmantree_path, denoise_exe)`.
- `get_arguments(instance, settings)` → native `denoise_batch` flags:
  `-a 0 -v --clean-alpha --progress [-cf] [--tiles X Y] -o <dir>/denoised
  <input> <start>-<end>`. No wrapper path, no `--rmantree-*`, no `--rename`.
- `get_environment(settings)` → `{"RMANTREE": rmantree_path}` plus
  `"PIXAR_LICENSE_FILE": pixar_license` when set.
- `validate` → require `rmantree_path`.
- Keep `_frame_count`, `_count_custom_frames`, `detect_large_image`,
  `_iter_render_products`.

### OIDN backend (`denoisers/oidn.py`)

- `get_executable` inherited (OIDN pool's `python_executable`).
- `get_arguments` → `<wrapper> --oidn-exe <join_bin(oidn_root,oidn_exe)>
  --oiiotool <join_bin(oiio_root,oiio_exe)> --input … --output-dir … --frame-*
  --beauty/albedo/normal-channel … <rename pairs> --verbose`. No per-OS flags.
- `validate` → wrapper path + non-empty `oidn_root_path` + `oiio_root_path` +
  guide channels.

### Combine plugin (`plugins/publish/houdini/extract_oiio_combine.py`)

- `Executable` = `combine.python_executable`.
- Emit `--oiiotool <join_bin(combine.oiio_root_path, combine.oiio_exe)>`
  instead of the per-OS triplet. Wrapper path via `resolve_wrapper_path`.

### Wrapper scripts

- **DELETE** `client/luma_denoise/scripts/renderman_denoise.py` (unused).
- `oidn_denoise.py`: remove `--oidn-root-*`/`--oiio-root-*`/`--oidn-exe-name`/
  `--oiio-exe-name` flags, `current_platform`/`build_tool_path`/`_pick_root`/
  `resolve_tools`/`_SYSTEM`/`import platform`. Make `--oidn-exe` and
  `--oiiotool` REQUIRED (full resolved paths). In `_run`, derive the OIDN bin
  dir from `--oidn-exe` (`os.path.dirname(oidn_exe)`) and prepend it to PATH so
  `oidnDenoise` finds its libs; drop the old root-based env logic.
- `oiio_combine.py`: remove the same per-OS machinery; make `--oiiotool`
  REQUIRED; `_run` uses `args.oiiotool` directly (no resolution block).

## Tests

- **DELETE** `tests/test_renderman_denoise.py`.
- `tests/test_denoiser_backends.py`: rewrite `RM_SETTINGS`/`OIDN_SETTINGS` to
  single-value per-step shape; RenderMan tests assert the native
  `denoise_batch` executable, native args, and `RMANTREE`/`PIXAR_LICENSE_FILE`
  env; OIDN tests assert resolved `--oidn-exe`/`--oiiotool` and no per-OS flags;
  remove `platform_triplet_args`/`resolve_platform_value` tests; add a
  `join_bin` test.
- `tests/test_oidn_denoise.py`: drop per-OS/`resolve_tools` tests; assert
  `--oidn-exe`/`--oiiotool` required and used directly.
- `tests/test_oiio_combine.py`: drop `test_build_tool_path_*` and
  `test_parse_args_accepts_oiio_root_flags`; `--oiiotool` required.

## Version & docs

- `package.py` → `0.7.0`.
- `CLAUDE.md`: update settings + denoiser descriptions — RenderMan runs
  `denoise_batch` directly (no Python); OIDN/combine use per-step single-value
  paths; Shared Tools holds only `scripts_directory`; per-OS/worker-side
  resolution removed.

## Out of scope / unchanged

- The combine channel/exclude/rename logic and `.combine.json` manifest.
- The denoise→combine Deadline dependency chain.
- OIDN's beauty/albedo/normal extraction algorithm.
- Combine's denoise-sidecar fallback (now the primary path for RenderMan, since
  RenderMan no longer writes a sidecar — combine uses the backend
  `beauty_rename_map` from settings, functionally identical).
