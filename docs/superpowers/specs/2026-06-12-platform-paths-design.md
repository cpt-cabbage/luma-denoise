# Design: Per-Platform Paths with Per-Step Worker Platform

**Date:** 2026-06-12
**Status:** Approved
**Target version:** 0.4.0 (breaking settings change)
**Follows:** 2026-06-12-settings-ux-restructure-design.md (shipped in 0.3.0)

## Problem

All tool/wrapper paths are single strings, but the farm is mixed-platform
(RenderMan denoise pools are Linux; combine pools are Windows). Paths are
baked into Deadline jobs at submission time and executed on workers, so the
platform that must resolve a path is the WORKER's platform for that step —
not the submitting machine's.

## Decisions (user-approved)

- Add a `MultiplatformPathModel` (windows/linux/darwin string fields) and
  convert ALL tool + wrapper path fields AND executable-name fields.
- Resolution is per step via a "Worker Platform" enum dropdown: one in the
  Denoising group (default `linux`) and one in the OIIO Combine group
  (default `windows`).
- `pixar_license` stays single-valued (not platform-specific).

## Converted fields (with seeded defaults)

| Field | windows | linux | darwin |
|---|---|---|---|
| denoise.renderman.rmantree_path | C:/Program Files/Pixar/RenderManProServer-26.3 | /opt/pixar/RenderManProServer-26.3 | /Applications/Pixar/RenderManProServer-26.3 |
| denoise.renderman.denoise_exe | denoise_batch.exe | denoise_batch | denoise_batch |
| denoise.renderman.wrapper_script_path | "" | "" | "" |
| denoise.oidn.oidn_root_path | "" | /opt/oidn | "" |
| denoise.oidn.denoise_exe | oidnDenoise.exe | oidnDenoise | oidnDenoise |
| denoise.oidn.wrapper_script_path | "" | "" | "" |
| combine.wrapper_script_path | "" | "" | "" |
| shared.python_executable | python | python | python |
| shared.oiio_root_path | "" | /opt/oiio | "" |
| shared.oiio_exe | oiiotool.exe | oiiotool | oiiotool |

New enum fields: `denoise.worker_platform` (default "linux", placed after
`group`), `combine.worker_platform` (default "windows", placed after
`group`). Enum values: windows / linux / darwin (label "macOS").

## Client resolution

One tolerant module-level helper in `denoisers/base.py`:

```python
def resolve_platform_value(value, worker_platform):
    # dict {windows,linux,darwin} -> value for the platform ('' if unset)
    # plain string -> passed through (pre-0.4.0 compatibility)
```

- Backends resolve every converted field with
  `denoise.worker_platform` (helper `DenoiserBackend._worker_platform`).
- `ExtractOiioCombine` imports `resolve_platform_value` and resolves
  `shared.*` + `combine.wrapper_script_path` with `combine.worker_platform`.
- Empty resolved wrapper paths raise actionable errors that NAME the
  platform: "...'denoise.renderman.wrapper_script_path' has no value for
  worker platform 'linux'...".
- Empty resolved tool roots/exes fall back to the same hardcoded defaults
  as today (so a fresh unsaved bundle still behaves).

## Unchanged

Wrapper scripts, manifest contract, `oiio_combine.py`,
`luma_denoise_publish.py` (touches no paths), rename-map flow, job
submission logic.

## Versioning

`package.py` -> 0.4.0. Settings entered once into the final layout
(nothing was entered for 0.2.0/0.3.0).
