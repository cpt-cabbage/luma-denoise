# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

`luma-denoise` is a custom AYON addon for Luma Studios that automates post-render processing of EXR files from Houdini USD renders. It submits chained Deadline jobs for:

1. **Pixar RenderMan denoising** of beauty passes
2. **OIIO combining** of denoised beauty + original AOVs into final EXRs for publish

The addon also provides AYON Launcher actions for opening renders in RV and launching Luma Tools.

## Build / Package / Deploy

```bash
# Build addon zip for upload to AYON server
python create_package.py

# Build with debug logging
python create_package.py --debug

# Build server folder only (no zip)
python create_package.py --skip-zip

# Extract only client code (for dev — requires -o)
python create_package.py --only-client -o /path/to/output
```

Output goes to `package/luma-denoise-{version}.zip`. Upload via AYON server UI or `ayon_api`.

Run tests with `python -m pytest tests -v` (no linter or CI pipeline). Python target is 3.9 (see `.python-version`).

## Version Management

Single source of truth: `package.py` (`version = "0.0.10"`).

`create_package.py` auto-generates `client/luma_denoise/version.py` at build time from `package.py`, so you only need to update `package.py` when bumping versions.

## Architecture

### Server Side (`server/`)

- `__init__.py` — `LumaDenoiseAddon(BaseServerAddon)` with `LumaDenoiseSettings` as its settings model. No custom endpoints.
- `settings.py` — Pydantic settings model with three groups: Denoising (denoiser dropdown + per-backend config incl. beauty rename maps), OIIO Combine (combine job + pass-through rename map), Shared Tools (worker python, OIIO paths). Tool install roots (RenderMan/OIDN/OIIO) are per-OS (windows/linux/darwin) and resolved on the worker at runtime; wrapper/python/library paths are single values handled by Deadline Path Mapping. No submit-time platform choice.

### Client Side (`client/luma_denoise/`)

- `addon.py` — `LumaDenoiseAddon(AYONAddon, IPluginPaths)`. Registers publish plugins only for Houdini host; registers launcher actions globally.

### Publish Plugin Chain (Houdini only)

All plugins live in `client/luma_denoise/plugins/publish/houdini/` and run during the Pyblish publish pipeline for `usdrender` family instances:

| Order | Plugin | Purpose |
|-------|--------|---------|
| Collector -0.49 | `CollectRestoreAyonBackup` | Restores AYON instance parms from HDA userData if wiped by definition update |
| Collector +0.35 | `CollectForceRenderDefaults` | Forces studio defaults (farm_split, review=True) on publish attributes |
| Collector +0.49 | `CollectBackupAyonParms` | Backs up current AYON parms to HDA userData |
| Validator | `ValidateLumaHda` | Reads HDA parms (engine, legacyexr, autocrop, procedurals, denoise) and stores in instance.data |
| Integrator -0.02 | `InjectHuskParameters` | Builds `plugin_info_data` dict (Engine, AllowedProcedurals, Autocrop, ExrMode) for Deadline |
| Integrator +0.1 | `LumaDenoiseUsdRender` | Submits denoise job (backend from `denoiser` setting: RenderMan or OIDN) to Deadline (depends on render job). Stores `denoise_job_id` |
| Integrator +0.11 | `ExtractOiioCombine` | Submits OIIO combine job to Deadline (depends on denoise or render job). Stores `oiio_combine_job_id` |

### Deadline Job Dependency Chain

```
Render Job (from ayon-deadline)
  -> Denoise Job (CommandLine plugin, Pixar denoise_batch)  [if denoise enabled]
    -> OIIO Combine Job (CommandLine plugin, oiiotool)      [always, if oiio_enabled]
```

Both `LumaDenoiseUsdRender` and `ExtractOiioCombine` extend `abstract_submit_deadline.AbstractSubmitDeadline` from `ayon-deadline` and use the Deadline `CommandLine` plugin.

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
to the active backend's `beauty_rename_map` setting when absent). Wrapper scripts
deploy to a shared filesystem; per-backend `wrapper_script_path` settings
locate them ({version} token supported).

### Launcher Actions (`client/luma_denoise/plugins/actions/`)

- `OpeninRV` — Opens latest render version in OpenRV (compositing/lookdev/fx tasks only)
- `StartshotTools` — Launches Luma Tools external app. Ctrl+click shows settings dialog with dev mode toggle (persisted in `~/.luma_tools_config.json`)

### Key Data Flow Through instance.data

- `instance.data["denoise"]` — bool, set by `ValidateLumaHda` from publish attributes
- `instance.data["detected_husk_engine"]` — "cpu" or "xpu", from HDA parm
- `instance.data["deadlineSubmissionJob"]["_id"]` — render job ID (set by ayon-deadline)
- `instance.data["denoise_job_id"]` — denoise Deadline job ID (set by `LumaDenoiseUsdRender`)
- `instance.data["oiio_combine_job_id"]` — combine Deadline job ID (set by `ExtractOiioCombine`)
- `instance.data["denoise_backend"]` — "renderman" or "oidn", set by `LumaDenoiseUsdRender`
- `instance.data["passdict"]` — AOV pass dictionary for OIIO combining

### OIIO Combine: Two Modes

1. **Pass builder mode** — If a `shot_data/{shot_name}.json` passes file exists in the task directory AND the `render_service` module is importable, uses `build_oiio_command()` for full control over pass configuration.
2. **Settings fallback** — Uses AOV toggles from server settings (diffuse, specular, crypto, normals, position, etc.) to build the oiiotool channel arguments directly.

## Dependencies

This addon depends on other AYON addons at runtime (declared in `package.py`):
- `core >= 1.6.7`
- `applications >= 1.2.4`
- `deadline >= 0.5.18-ls.0.1.0` (Luma fork)

Houdini-specific imports (`hou`, `pxr.Usd`, `pxr.UsdRender`) are used only in publish plugins and are imported at call time to avoid errors in non-Houdini hosts.

## HDA Integration

The addon expects a Luma Render HDA in Houdini with these parameters:
- `engine` — CPU/XPU renderer selection
- `legacyexr` — EXR mode toggle
- `autocrop` — AOV autocrop toggle
- `enableprocedurals` — Procedurals toggle
- `rmdenoise_aovs` — Denoise enable (set by the addon during validation)

`ValidateLumaHda` walks up the node hierarchy from the ROP to find the HDA by checking for the `legacyexr` parm.

The AYON instance backup system (`collect_ayon_backup.py`) protects against HDA definition updates wiping editable node spare parameters, storing a JSON backup in the HDA's `userData` under key `ayon_instance_backup`.
