# Design: Multi-Denoiser Abstraction (RenderMan + OIDN)

**Date:** 2026-06-12
**Status:** Approved
**Repo:** luma-denoise (AYON addon)
**Target version:** 0.2.0 (breaking settings change)

## Goal

Abstract the denoising step of the post-render pipeline so it supports multiple
denoiser backends. Today the pipeline hardcodes Pixar RenderMan's
`denoise_batch`. This design adds Intel Open Image Denoise (OIDN) as a second
backend behind a common abstraction, without changing the overall pipeline
shape:

```
Render job (ayon-deadline)
  -> Denoise job (selected backend)        [if denoise enabled]
    -> OIIO combine job                    [always, if oiio_enabled]
```

## Locked Decisions

| Decision | Choice |
|----------|--------|
| Denoiser selection | Project-settings dropdown (`denoiser` enum: `renderman` / `oidn`). Whole project uses one denoiser. |
| OIDN execution model | Python wrapper script on the Deadline worker (same deployment pattern as `oiio_combine.py`). |
| Output contract | Sidecar manifest (`<sequence>.denoise.json`) written by the denoise job and read by the combine job. |
| RenderMan manifest | RenderMan also gets a thin wrapper (`renderman_denoise.py`) so both backends honor the identical manifest contract. |
| Guide AOVs (OIDN) | Albedo + normal channels are **required**. The wrapper hard-fails with a clear error if they are missing from the render EXR. |
| Combine step | Stays a separate Deadline job after denoise for every backend. Cryptomattes and other non-color AOVs always pass through from the raw render via the combine step; no denoiser touches them. |

### Why a wrapper for OIDN

OIDN is a buffer-filtering library; its `oidnDenoise` CLI takes one image file
as a single 3-channel color buffer, with albedo and normal supplied as
*separate files* (`--alb`, `--nrm`). It cannot select channels out of a packed
multi-channel render EXR the way `denoise_batch` can. The wrapper performs the
channel extraction/reassembly around the CLI call.

## 1. Backend Abstraction — `client/luma_denoise/denoisers/`

New package with one strategy class per denoiser.

### `base.py` — `DenoiserBackend` (abstract base)

Each backend provides:

- `name: str` — `"renderman"` or `"oidn"`.
- `requires_combine: bool` — `True` for both current backends. Future-proofing
  flag for a hypothetical denoiser that writes final EXRs itself.
- `get_executable(settings) -> str` — what the Deadline CommandLine job runs.
  For both backends this is the worker Python (existing top-level
  `python_executable` setting), because both run wrapper scripts.
- `get_arguments(instance, settings) -> str` — full wrapper CLI arguments:
  input/output paths, frame range, and backend-specific flags.
- `get_environment(settings) -> dict[str, str]` — env vars for the Deadline job.
- `validate(instance, settings) -> None` — submission-time checks; raises with
  an actionable message on missing configuration (wrapper path empty, etc.).

Backends are pure: they compute data from (instance data, settings) and do not
talk to Deadline. All submission mechanics stay in the Pyblish plugin.

### `renderman.py` — `RendermanDenoiser`

Ports today's logic from `LumaDenoiseUsdRender` verbatim:

- Cross-frame denoising (`-cf`) when the frame count (including custom-frames
  publish attribute) is >= 8.
- Tiled denoising (`--tiles 2 2`) when any render-product resolution meets the
  `tiled_denoise_threshold` — the USD inspection helpers (`detectlargeimage`,
  `get_expected_resolution`, `iter_render_products`) move from the Pyblish
  plugin into this backend since only RenderMan uses them.
- Environment: `RMANTREE`, `PIXAR_LICENSE_FILE`, RenderMan `bin` on `PATH`.

### `oidn.py` — `OidnDenoiser`

- Arguments target `oidn_denoise.py` (wrapper): raw EXR pattern, output dir,
  frame range, `oidnDenoise` executable path, albedo/normal channel names.
- Environment: OIDN install `bin` directory on `PATH`.

### `__init__.py`

`get_denoiser_backend(name) -> DenoiserBackend` registry lookup; raises
`KeyError`-style clear error for unknown names.

## 2. Submission Plugin — `LumaDenoiseUsdRender` (modified)

Remains the only denoise Pyblish plugin (order `IntegratorOrder + 0.1`,
`usdrender` family, Houdini host). Unchanged: render-job dependency wiring,
`denoise_job_id` bookkeeping, `get_generic_job_info`, `process_submission()`,
output dir handling.

Changes:

- Reads `denoiser` from `project_settings["luma-denoise"]` and resolves the
  backend via the registry.
- Delegates executable, arguments, and env construction to the backend.
- Calls `backend.validate(instance, settings)` before submitting.
- Job name gains the backend: `... [DENOISE:RENDERMAN]` / `[DENOISE:OIDN]`.
- Stores `instance.data["denoise_backend"] = backend.name`.

`ValidateLumaHda`, the collectors, and `InjectHuskParameters` are untouched.

## 3. Settings Restructure (`server/settings.py`) — BREAKING

```
denoise_enabled: bool                          (unchanged)
denoiser: enum ["renderman", "oidn"]           NEW — default "renderman"
denoise_deadline_priority: int                 (unchanged, shared)
denoise_pool: str                              (unchanged, shared)
denoise_group: str                             (unchanged, shared)

renderman: RendermanDenoiserSettings           NEW group (moved fields)
    rmantree_path        (was denoise_rmantree_path)
    denoise_exe          (was top-level denoise_exe, default "denoise_batch")
    pixar_license        (was denoise_pixar_lic)
    tiled_denoise_threshold  (moved from top level)
    wrapper_script_path  NEW — shared-FS path to renderman_denoise.py,
                         supports {version} token (same contract as the
                         existing combine wrapper_script_path)

oidn: OidnDenoiserSettings                     NEW group
    oidn_root_path       default "/opt/oidn"
    denoise_exe          default "oidnDenoise"
    wrapper_script_path  shared-FS path to oidn_denoise.py, {version} token
    albedo_channel       default "albedo" — exact channel (layer) name in the
                         render EXR holding the albedo guide
    normal_channel       default "N" — exact channel (layer) name holding the
                         normal guide
```

- The existing top-level `python_executable` is reused by the denoise wrappers
  (it already drives the combine wrapper).
- Old flat fields (`denoise_rmantree_path`, `denoise_exe`,
  `denoise_pixar_lic`, top-level `tiled_denoise_threshold`) are **removed**.
  No automated migration: values are re-entered once in the AYON server
  settings UI after upgrading. Defaults match current production values to
  minimize re-entry.
- All OIIO-combine settings are unchanged.

## 4. Wrapper Scripts — `client/luma_denoise/scripts/`

Both deploy exactly like `oiio_combine.py`: copied to a shared filesystem,
referenced by a `wrapper_script_path` setting with a `{version}` token
substituted at submission time.

### `renderman_denoise.py` (thin)

1. Builds and runs the `denoise_batch` command (args passed in by the backend:
   `-a 0 -v --clean-alpha --progress [-cf] [--tiles X Y] -o <outdir> <input> <range>`).
2. Propagates `denoise_batch`'s exit code (non-zero -> Deadline job fails).
3. On success, writes the sidecar manifest describing its output
   (RenderMan convention: `Ci.r/Ci.g/Ci.b/a.Z` beauty channels).

### `oidn_denoise.py`

Per frame in the assigned range:

1. Inspect the raw EXR's channel list (reuse the `read_channels` pattern from
   `oiio_combine.py`: OIIO Python module if importable, `oiiotool --info -v`
   subprocess fallback).
2. **Hard-fail** (non-zero exit, explicit error naming the missing channel and
   the settings field that configures it) if the configured albedo or normal
   channels are absent.
3. Extract beauty, albedo, and normal channel groups to temporary single-layer
   EXRs via `oiiotool`.
4. Run `oidnDenoise --hdr <beauty.exr> --alb <albedo.exr> --nrm <normal.exr> -o <out.exr>`.
5. Write the denoised frame into `<renderdir>/denoised/<same filename>`.
6. Remove temporaries (respect a `--keep-temps` debug flag).

After the last frame, write the sidecar manifest.

### Manifest contract

One `<sequence>.denoise.json` per render sequence (frame token stripped from
the sidecar name, same normalization as the existing combine manifest), written
next to the denoised frames:

```json
{
  "denoiser": "oidn",
  "addon_version": "0.2.0",
  "source_pattern": ".../render.####.exr",
  "output_pattern": ".../denoised/render.####.exr",
  "beauty_channel_map": {"Ci.r": "R", "Ci.g": "G", "Ci.b": "B", "a.Z": "A"},
  "guide_channels": {"albedo": "albedo", "normal": "N"},
  "frames": [1001, 1100]
}
```

`beauty_channel_map` maps the channel names as they exist in the denoised
output EXRs to the final compositing names the combine step should produce.
`guide_channels` is informational (OIDN only; omitted by RenderMan).

## 5. Combine Integration

`oiio_combine.py` gains manifest **reading**:

- Auto-discovers `*.denoise.json` in the denoised directory.
- When found, `beauty_channel_map` from the manifest **overrides** the static
  `beauty_rename_map_denoised` setting.
- When absent, falls back to the settings rename map (keeps old renders,
  pass-through mode, and partial deployments working).

`ExtractOiioCombine` (the Pyblish plugin) needs no changes.

## 6. Error Handling

- **Submission time:** `backend.validate()` raises before any Deadline call if
  the selected backend's wrapper path or required settings are empty —
  actionable message naming the exact settings field (same pattern as the
  existing combine wrapper-path error).
- **Farm time:** wrappers exit non-zero on missing guide AOVs, missing
  executables, or denoiser failure. Deadline fails the denoise job, the
  dependent combine job never runs, and nothing half-denoised reaches publish.
- **Unknown `denoiser` value:** registry lookup fails the publish with the
  list of known backends.

## 7. Testing

Follow the existing `tests/test_oiio_combine.py` style (pytest, subprocess and
OIIO mocked):

- `tests/test_renderman_denoise.py` — command construction, exit-code
  propagation, manifest writing.
- `tests/test_oidn_denoise.py` — channel detection, hard-fail on missing
  guides, extraction/denoise/reassembly command sequences, manifest writing.
- `tests/test_denoiser_backends.py` — backends are pure functions of
  (instance data, settings): assert executable/arguments/environment for both
  backends, validate() failure cases, registry lookup.

## Out-of-Repo Dependencies

- **HDA / render AOVs:** OIDN requires the render to emit albedo and normal
  AOVs. This is controlled by the Luma Render HDA / RenderMan display
  products, not by this addon (`ValidateLumaHda` only toggles
  `rmdenoise_aovs`). Until the HDA emits those AOVs, the OIDN path fails
  loudly by design.
- **Worker deployment:** `oidnDenoise` (OIDN release build) and `oiiotool`
  must be installed on Deadline workers at the configured paths; the two new
  wrapper scripts must be deployed to the shared filesystem alongside
  `oiio_combine.py`.

## Versioning

`package.py` bumps to `0.2.0` (new feature + breaking settings layout), per
the repo's single-source-of-truth version policy.
