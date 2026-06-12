# Design: Settings UX Restructure + Per-Denoiser Rename Maps

**Date:** 2026-06-12
**Status:** Approved
**Repo:** luma-denoise (AYON addon)
**Target version:** 0.3.0 (breaking settings change)
**Follows:** 2026-06-12-denoiser-abstraction-design.md (shipped in 0.2.0)

## Problem

After 0.2.0 the settings surface is confusing in the AYON server UI:

1. Three fields titled "Wrapper Script Path" (combine top-level, renderman,
   oidn) with nothing identifying which script each refers to.
2. No visible boundary between the denoise step's settings and the OIIO
   combine step's settings — one flat list.
3. `beauty_rename_map_denoised` defaults to RenderMan naming and lives in the
   combine section, but the denoise manifest is now the rename authority;
   as a fallback it is wrong whenever OIDN is the active denoiser.
4. Shared fields (`python_executable`, `oiio_root_path`, `oiio_exe`) are used
   by both the combine wrapper and OIDN's channel extraction but sit
   unmarked in the middle of the combine block.

AYON settings cannot conditionally hide a group based on a sibling enum, so
clarity must come from structure and naming.

## Decisions (user-approved)

- Full restructure into three top-level collapsible groups: **Denoising**,
  **OIIO Combine**, **Shared Tools**. Field-name prefixes dropped inside
  groups. Breaking change; settings are re-entered once (0.2.0 settings were
  never entered).
- Rename maps move **per denoiser** into each backend group; the backend
  passes its map to its wrapper as `--rename` pairs; the wrapper records
  exactly those pairs in the manifest's `beauty_channel_map`.
  `beauty_rename_map_denoised` is deleted; `beauty_rename_map_raw` stays in
  the combine group (pass-through has no denoiser).
- Version bumps to 0.3.0.

## 1. New settings model (`server/settings.py`)

```
LumaDenoiseSettings
├─ denoise: DenoiseSettings                 title "Denoising"
│   ├─ enabled: bool = False                "Enable Denoising"
│   ├─ denoiser: enum = "renderman"         "Denoiser" (renderman | oidn)
│   ├─ priority: int = 50                   "Deadline Priority"
│   ├─ pool: str = "luma"                   "Deadline Pool"
│   ├─ group: str = "denoise_group"         "Deadline Group"
│   ├─ renderman: RendermanDenoiserSettings "RenderMan Backend"
│   │   ├─ rmantree_path, denoise_exe, pixar_license,
│   │   │  tiled_denoise_threshold          (unchanged from 0.2.0)
│   │   ├─ wrapper_script_path              "RenderMan Wrapper Script Path (renderman_denoise.py)"
│   │   └─ beauty_rename_map: [ChannelRenamePair]
│   │        default Ci.r→R, Ci.g→G, Ci.b→B, a.Z→A
│   └─ oidn: OidnDenoiserSettings           "OIDN Backend"
│       ├─ oidn_root_path, denoise_exe,
│       │  beauty_channel, albedo_channel, normal_channel  (unchanged)
│       ├─ wrapper_script_path              "OIDN Wrapper Script Path (oidn_denoise.py)"
│       └─ beauty_rename_map: [ChannelRenamePair]
│            default beauty.r→R, beauty.g→G, beauty.b→B, a.Z→A
├─ combine: CombineSettings                 title "OIIO Combine"
│   ├─ enabled: bool = True                 "Enable OIIO Combine"
│   ├─ priority: int = 50, pool: "default", group: "default"
│   ├─ wrapper_script_path                  "Combine Wrapper Script Path (oiio_combine.py)"
│   ├─ run_when_denoise_disabled: bool = False
│   ├─ channel_exclude_patterns             (unchanged default)
│   ├─ beauty_rename_map_raw                (unchanged default; title
│   │                                        "Beauty Rename Map (raw pass-through)")
│   ├─ oiiotool_extra_args, output_compression, output_data_type (unchanged)
│   ├─ write_manifest: bool = True          (was write_combine_manifest)
│   ├─ verbose_logging: bool = True         (was wrapper_verbose_logging)
│   ├─ output_subdirectory: "combined", preserve_intermediates: False
└─ shared: SharedToolsSettings              title "Shared Tools"
    ├─ python_executable: "python"          "Python Executable (Deadline workers)"
    ├─ oiio_root_path: "/opt/oiio"          "OIIO Root Path"
    └─ oiio_exe: "oiiotool"                 "oiiotool Executable Name"
```

Every wrapper-path title names its script. Group descriptions explain which
Deadline job each section drives.

## 2. Rename-map data flow

```
settings denoise.<backend>.beauty_rename_map
   │ backend.get_arguments appends --rename SRC=DST per pair
   ▼
denoise wrapper (renderman_denoise.py / oidn_denoise.py)
   │ --rename pairs, when present, BECOME the manifest beauty_channel_map
   │ (no pairs → wrapper falls back to its built-in derivation:
   │  RENDERMAN_BEAUTY_MAP / derived-from-channels + a.Z→A)
   ▼
<seq>.denoise.json  beauty_channel_map
   │ read by oiio_combine.py (already implemented, unchanged)
   ▼
combined EXR channel names
```

No-manifest fallback in `ExtractOiioCombine`: when denoise ran, the CLI
`--rename` pairs passed to `oiio_combine.py` come from the **active**
backend's map — `instance.data["denoise_backend"]` (falling back to the
`denoise.denoiser` setting) selects `denoise.<backend>.beauty_rename_map`.
When denoise did not run (pass-through), `combine.beauty_rename_map_raw` is
used as today.

## 3. Client changes

- **`denoisers/base.py`** — `get_executable` reads
  `settings["shared"]["python_executable"]`; `_backend_settings` reads
  `settings["denoise"][self.name]`; `_resolve_wrapper_path` error message
  names `denoise.<name>.wrapper_script_path`. New helper
  `rename_pair_args(settings)` returning the backend map as a flat
  `["--rename", "SRC=DST", ...]` argument list.
- **`denoisers/renderman.py` / `oidn.py`** — read Deadline-agnostic config
  from the new nested paths; OIDN's oiiotool comes from `shared`; both
  append the rename-pair args. `validate` error paths updated.
- **Wrappers** — both gain `--rename SRC=DST` (repeatable). Parsed pairs,
  when present, are used verbatim as the manifest `beauty_channel_map`;
  current built-in behavior is the no-pairs fallback (keeps old-style
  invocations working).
- **`luma_denoise_publish.py`** — backend methods keep receiving the FULL
  addon settings dict (contract unchanged); job priority/pool/group from
  `denoise.*`; `get_denoise_enabled` reads `denoise.enabled`; `denoiser`
  read from `denoise.denoiser`.
- **`extract_oiio_combine.py`** — all reads move to `combine.*` /
  `shared.*`; the oiiotool path becomes
  `<shared.oiio_root_path>/bin/<shared.oiio_exe>` (the hardcoded
  `oiiotool.exe` suffix is removed — on Windows worker pools set
  `shared.oiio_exe` to `oiiotool.exe`); rename-pair selection per section 2.
- **`oiio_combine.py`** — NO changes (manifest-first logic already correct).

## 4. Out of scope

- Conditional show/hide of backend groups (unsupported by AYON settings).
- Any change to job submission logic, manifest schema, or the publish chain.

## 5. Versioning & deployment notes

- `package.py` → 0.3.0.
- Settings must be (re-)entered once after upload — nothing carries over
  from the flat layout (it was never populated in production).
- Windows combine workers: set `shared.oiio_exe = "oiiotool.exe"` —
  the previous build hardcoded the `.exe` suffix; now it is explicit.
- Visual polish caveat: exact group rendering is confirmed on the server
  after upload; title/`section` tweaks may follow as a patch.

## 6. Testing

- Backend tests: fixtures move to the nested settings shape; new assertions
  that `--rename Ci.r=R ...` / `--rename beauty.r=R ...` appear in
  `get_arguments` output.
- Wrapper tests: `--rename` pairs override the manifest map; no pairs →
  existing built-in behavior (regression-guarded by existing tests).
- Combine plugin has no local tests (AYON imports unavailable) —
  `py_compile` + greps + review, as in 0.2.0.
