# Design: Worker-Side Path Resolution

**Date:** 2026-06-15
**Status:** Approved
**Target version:** 0.5.0 (breaking settings change)
**Supersedes the submit-time approach in:** 2026-06-12-platform-paths-design.md (0.4.0)

## Context that drives this

The Deadline farm is **mixed-OS**: a single pool/group can run the same job
on a Windows worker one time and a Linux worker the next. The platform is
unknown until a worker picks the job up. **Deadline Path Mapping is enabled**
and covers the project/library mounts (where render frames and the wrapper
scripts live) but NOT the renderer/tool install roots (RenderMan, OIDN, OIIO),
which install to different, unmapped locations per OS.

0.4.0 resolved every path at submission time via a per-step "Worker Platform"
dropdown. On a mixed pool that bakes in the wrong platform. It also rendered a
confusing UI (stacked windows/linux/macOS inputs with the parent field title
hidden).

## Principle

Split paths into two buckets by **who needs them**:

- **Things Deadline launches** — the Python interpreter and the wrapper `.py`
  path. Single canonical values; **Deadline Path Mapping** translates them per
  worker (they live on mapped mounts / resolve on PATH). No platform choice.
- **Things the wrapper runs** — `denoise_batch` / `oidnDenoise` / `oiiotool`.
  The wrapper is already executing on the real worker, so it resolves these
  **at runtime by its own `platform.system()`**. The submission passes all
  three per-OS install roots; the wrapper picks. Works on a mixed pool.

Render input/output paths also live on mapped storage, so Path Mapping
rewrites them per worker — unchanged, no action needed.

## Settings model (0.5.0)

Remove both `worker_platform` enums (DenoiseSettings, CombineSettings).

Revert to single-value `str` (Path Mapping / PATH resolves them):
- `denoise.renderman.wrapper_script_path`
- `denoise.oidn.wrapper_script_path`
- `combine.wrapper_script_path`
- `shared.python_executable`
- `denoise.renderman.denoise_exe` (default "denoise_batch")
- `denoise.oidn.denoise_exe` (default "oidnDenoise")
- `shared.oiio_exe` (default "oiiotool")

Keep as per-OS `MultiplatformPathModel` (genuinely per-OS, unmapped installs):
- `denoise.renderman.rmantree_path`
- `denoise.oidn.oidn_root_path`
- `shared.oiio_root_path`

Drop `_layout = "expanded"` from `MultiplatformPathModel` so the parent field
title renders (fixes the missing-label UI). The model keeps the
windows/linux/darwin fields.

`pixar_license`, `tiled_denoise_threshold`, channel names, rename maps,
priority/pool/group, and all combine knobs are unchanged.

## Executable name + `.exe`

Exe names are single values. The wrapper appends `.exe` on Windows when the
name lacks an extension. This removes the per-OS exe triplets entirely.

## Client changes

### `denoisers/base.py`
- `get_executable` → returns `shared.python_executable` as a plain string
  (no platform resolution), default "python".
- `_resolve_wrapper_path` → reads the backend's `wrapper_script_path` as a
  plain string, substitutes `{version}`, raises if empty (no platform in the
  message). `resolve_platform_value` stays (still tolerant) but is no longer
  used for bootstrap.
- Remove `_worker_platform` (no submit-time platform anywhere).
- `rename_pair_args` unchanged.

### `denoisers/renderman.py`
- `get_arguments` passes the THREE rmantree roots and the single exe name,
  not a resolved path:
  `--rmantree-windows <w> --rmantree-linux <l> --rmantree-darwin <d>
   --denoise-exe-name <name> --pixar-license <server>`
  plus the existing `--input/--output-dir/--frame-start/--frame-end/
  --addon-version/--cross-frame?/--tiles?/--rename.../--verbose`.
- `get_environment` → `{}` (RMANTREE/PATH/PIXAR_LICENSE_FILE now set by the
  wrapper at runtime, since the root is only known then).
- `detect_large_image` / frame counting unchanged.

### `denoisers/oidn.py`
- `get_arguments` passes per-OS oidn roots + oidn exe name AND per-OS oiio
  roots + oiio exe name:
  `--oidn-root-windows/linux/darwin <...> --oidn-exe-name <name>
   --oiio-root-windows/linux/darwin <...> --oiio-exe-name <name>`
  plus existing channel/frame/rename/verbose args.
- `get_environment` → `{}`.

### `plugins/publish/houdini/extract_oiio_combine.py`
- Remove `worker_platform` resolution. `python_exe` and `wrapper_template`
  become single-value reads (`shared.python_executable`,
  `combine.wrapper_script_path`).
- Pass per-OS oiio roots + single oiio exe name to the wrapper instead of a
  resolved `--oiiotool` path:
  `--oiio-root-windows/linux/darwin <...> --oiio-exe-name <name>`.

### `plugins/publish/houdini/luma_denoise_publish.py`
- No path changes. It still applies `get_environment()` (now `{}`, a no-op
  loop) and passes the full settings dict to the backend.

## Wrapper changes (each standalone — small duplicated helper, by design)

Shared helper pattern in each wrapper:

```python
import platform

def current_platform() -> str:
    return {"Windows": "windows", "Linux": "linux",
            "Darwin": "darwin"}.get(platform.system(), "linux")

def build_tool_path(root: str, exe_name: str, plat: str) -> str:
    exe = exe_name
    if plat == "windows" and not exe.lower().endswith(".exe"):
        exe = exe + ".exe"
    return f"{root.rstrip('/')}/bin/{exe}"
```

### `scripts/renderman_denoise.py`
- New args: `--rmantree-windows/linux/darwin`, `--denoise-exe-name`,
  `--pixar-license`. Drop `--denoise-exe`.
- At runtime: pick the rmantree root for `current_platform()`, build the
  denoise_batch path via `build_tool_path`, and set the subprocess env:
  `RMANTREE=<root>`, prepend `<root>/bin` to `PATH`, and
  `PIXAR_LICENSE_FILE=<--pixar-license>` if given.
- Fail fast (exit 1) if the root for the current platform is empty, naming
  the platform.

### `scripts/oidn_denoise.py`
- New args: `--oidn-root-windows/linux/darwin`, `--oidn-exe-name`,
  `--oiio-root-windows/linux/darwin`, `--oiio-exe-name`. Drop `--oidn-exe`
  and `--oiiotool`.
- At runtime: resolve oidn exe and oiiotool from the per-OS roots; prepend
  `<oidn_root>/bin` to PATH. Fail fast if a needed root is empty for the
  platform.

### `scripts/oiio_combine.py`
- New args: `--oiio-root-windows/linux/darwin`, `--oiio-exe-name`. Drop
  `--oiiotool`.
- At runtime: resolve the oiiotool path once and use it everywhere it
  currently uses `args.oiiotool`. Fail fast if the root is empty for the
  platform.

## Unchanged

Manifest contract, rename-map flow, frame handling, job dependency chain,
Deadline submission mechanics, launcher actions.

## Versioning & deployment

`package.py` -> 0.5.0. Configure once: fill each tool's three install roots
(reference data, clearly labeled now), set the wrapper/python paths as single
values (Path Mapping handles them), set exe names. No platform to choose.
