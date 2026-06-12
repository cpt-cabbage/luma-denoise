# Settings UX Restructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the addon settings into three clear top-level groups (Denoising / OIIO Combine / Shared Tools), move beauty rename maps per denoiser with a `--rename` flow into the wrappers' manifests, and bump to 0.3.0.

**Architecture:** Server settings become nested `denoise`/`combine`/`shared` groups. Backends keep receiving the FULL addon settings dict but read nested paths, and now emit `--rename SRC=DST` pairs which the wrappers record verbatim in the manifest `beauty_channel_map` (built-in derivation stays as the no-pairs fallback). `ExtractOiioCombine` re-paths all reads and selects its fallback CLI rename pairs from the ACTIVE backend's map. `oiio_combine.py` is untouched.

**Tech Stack:** Python 3.9, pytest, AYON Pydantic settings.

**Spec:** `docs/superpowers/specs/2026-06-12-settings-ux-restructure-design.md`

---

## File Map

| Action | Path |
|--------|------|
| Rewrite | `server/settings.py` |
| Modify | `client/luma_denoise/denoisers/base.py` (nested paths + `rename_pair_args`) |
| Modify | `client/luma_denoise/denoisers/renderman.py`, `oidn.py` (nested paths + rename args) |
| Modify | `client/luma_denoise/scripts/renderman_denoise.py`, `oidn_denoise.py` (`--rename`) |
| Modify | `client/luma_denoise/plugins/publish/houdini/luma_denoise_publish.py` (nested reads) |
| Modify | `client/luma_denoise/plugins/publish/houdini/extract_oiio_combine.py` (nested reads + fallback map selection) |
| Modify | `tests/test_denoiser_backends.py`, `tests/test_renderman_denoise.py`, `tests/test_oidn_denoise.py` |
| Modify | `package.py` (0.3.0), `CLAUDE.md` |
| NO CHANGE | `client/luma_denoise/scripts/oiio_combine.py`, `tests/test_oiio_combine.py` |

Run all tests: `python -m pytest tests -v` (from repo root). Currently 78 pass.

---

### Task 1: Rewrite `server/settings.py`

**Files:** Modify: `server/settings.py`

- [ ] **Step 1:** Replace the entire content of `server/settings.py` with the model below. Keep the existing import block (lines 1–27) and the `ChannelRenamePair` class exactly as they are; everything after `ChannelRenamePair` is replaced:

```python
def _denoiser_enum():
    return [
        {"value": "renderman", "label": "Pixar RenderMan (denoise_batch)"},
        {"value": "oidn", "label": "Intel Open Image Denoise (OIDN)"},
    ]


def _output_data_type_enum():
    return ["preserve", "float", "half"]


class RendermanDenoiserSettings(BaseSettingsModel):
    """Pixar RenderMan denoise_batch backend."""

    rmantree_path: str = SettingsField(
        "/opt/pixar/RenderManProServer-26.3",
        title="RenderMan Root Path",
        description="Path to RMANTREE on the Deadline workers.",
    )

    denoise_exe: str = SettingsField(
        "denoise_batch",
        title="Denoiser Executable Name",
        description="Name of the RenderMan denoiser executable in <RMANTREE>/bin.",
    )

    pixar_license: str = SettingsField(
        "9010@192.168.35.28",
        title="RenderMan License Server",
        description="RenderMan license server or file location.",
    )

    tiled_denoise_threshold: int = SettingsField(
        2048,
        title="Tiled Denoise Resolution Threshold",
        description=(
            "Minimum resolution (width or height) at which tiled denoising "
            "is enabled. Images with either dimension at or above this value "
            "will be denoised in tiles to reduce memory usage."
        ),
    )

    wrapper_script_path: str = SettingsField(
        "",
        title="RenderMan Wrapper Script Path (renderman_denoise.py)",
        description=(
            "Absolute path to renderman_denoise.py on a shared filesystem "
            "accessible from every Deadline render node. Supports the "
            "{version} token - substituted at submission time with the "
            "luma-denoise addon version. MUST be configured for the "
            "RenderMan denoise step to submit."
        ),
    )

    beauty_rename_map: list[ChannelRenamePair] = SettingsField(
        default_factory=lambda: [
            ChannelRenamePair(source="Ci.r", target="R"),
            ChannelRenamePair(source="Ci.g", target="G"),
            ChannelRenamePair(source="Ci.b", target="B"),
            ChannelRenamePair(source="a.Z", target="A"),
        ],
        title="Beauty Rename Map",
        description=(
            "How this denoiser's output channels are renamed in the final "
            "combined EXR (RenderMan Ci/a convention -> Nuke R/G/B/A). "
            "Recorded in the denoise manifest and consumed by the OIIO "
            "combine step."
        ),
    )


class OidnDenoiserSettings(BaseSettingsModel):
    """Intel Open Image Denoise backend."""

    oidn_root_path: str = SettingsField(
        "/opt/oidn",
        title="OIDN Root Path",
        description="Path to the OIDN install root on the Deadline workers.",
    )

    denoise_exe: str = SettingsField(
        "oidnDenoise",
        title="Denoiser Executable Name",
        description="Name of the OIDN executable in <root>/bin.",
    )

    wrapper_script_path: str = SettingsField(
        "",
        title="OIDN Wrapper Script Path (oidn_denoise.py)",
        description=(
            "Absolute path to oidn_denoise.py on a shared filesystem "
            "accessible from every Deadline render node. Supports the "
            "{version} token. MUST be configured for the OIDN denoise "
            "step to submit."
        ),
    )

    beauty_channel: str = SettingsField(
        "beauty",
        title="Beauty Layer Name",
        description=(
            "Layer name of the beauty channels in the raw render EXR "
            "(e.g. 'beauty' for beauty.r/beauty.g/beauty.b)."
        ),
    )

    albedo_channel: str = SettingsField(
        "albedo",
        title="Albedo Guide Layer Name",
        description=(
            "Layer name of the albedo guide AOV. REQUIRED: the OIDN "
            "denoise job fails if this layer is missing from the render."
        ),
    )

    normal_channel: str = SettingsField(
        "N",
        title="Normal Guide Layer Name",
        description=(
            "Layer name of the normal guide AOV. REQUIRED: the OIDN "
            "denoise job fails if this layer is missing from the render."
        ),
    )

    beauty_rename_map: list[ChannelRenamePair] = SettingsField(
        default_factory=lambda: [
            ChannelRenamePair(source="beauty.r", target="R"),
            ChannelRenamePair(source="beauty.g", target="G"),
            ChannelRenamePair(source="beauty.b", target="B"),
            ChannelRenamePair(source="a.Z", target="A"),
        ],
        title="Beauty Rename Map",
        description=(
            "How this denoiser's output channels are renamed in the final "
            "combined EXR. OIDN output keeps the source beauty layer names. "
            "Recorded in the denoise manifest and consumed by the OIIO "
            "combine step."
        ),
    )


class DenoiseSettings(BaseSettingsModel):
    """The denoise Deadline job, submitted after the render job."""

    enabled: bool = SettingsField(
        False,
        title="Enable Denoising",
        description="Enable automatic denoising of rendered EXR files.",
    )

    denoiser: str = SettingsField(
        "renderman",
        title="Denoiser",
        description=(
            "Which denoiser backend processes the rendered EXRs. Configure "
            "the matching backend group below."
        ),
        enum_resolver=_denoiser_enum,
    )

    priority: int = SettingsField(
        50,
        title="Deadline Priority",
        description="Priority of the denoise Deadline job.",
    )

    pool: str = SettingsField(
        "luma",
        title="Deadline Pool",
        description="Pool of the denoise Deadline job.",
    )

    group: str = SettingsField(
        "denoise_group",
        title="Deadline Group",
        description="Group of the denoise Deadline job.",
    )

    renderman: RendermanDenoiserSettings = SettingsField(
        default_factory=RendermanDenoiserSettings,
        title="RenderMan Backend",
        description="Used when Denoiser is set to Pixar RenderMan.",
    )

    oidn: OidnDenoiserSettings = SettingsField(
        default_factory=OidnDenoiserSettings,
        title="OIDN Backend",
        description="Used when Denoiser is set to Intel Open Image Denoise.",
    )


class CombineSettings(BaseSettingsModel):
    """The OIIO combine Deadline job, submitted after the denoise job."""

    enabled: bool = SettingsField(
        True,
        title="Enable OIIO Combine",
        description=(
            "Enable the OIIO combine job that merges denoised beauty with "
            "the untouched AOVs (crypto, depth, ...) from the raw render."
        ),
    )

    priority: int = SettingsField(
        50,
        title="Deadline Priority",
        description="Priority of the combine Deadline job.",
    )

    pool: str = SettingsField(
        "default",
        title="Deadline Pool",
        description="Pool of the combine Deadline job.",
    )

    group: str = SettingsField(
        "default",
        title="Deadline Group",
        description="Group of the combine Deadline job.",
    )

    wrapper_script_path: str = SettingsField(
        "",
        title="Combine Wrapper Script Path (oiio_combine.py)",
        description=(
            "Absolute path to oiio_combine.py on a shared filesystem "
            "accessible from both the submitting machine AND every Deadline "
            "render node. Supports the {version} token. MUST be configured "
            "for the OIIO combine step to submit."
        ),
    )

    run_when_denoise_disabled: bool = SettingsField(
        False,
        title="Run Combine when denoise is disabled",
        description=(
            "Only applies when denoise did NOT run for an instance. "
            "When False (default), the combine job is skipped entirely "
            "and publish pulls from the raw render directory. When True, "
            "the combine job runs as a pass-through over the raw render. "
            "When denoise did run, the combine job always runs."
        ),
    )

    channel_exclude_patterns: list[str] = SettingsField(
        default_factory=lambda: ["*_mse", "mse", "sampleCount"],
        title="Channel Exclude Patterns",
        description=(
            "fnmatch glob patterns. Any raw-render channel whose name "
            "matches any pattern is excluded from the combined output. "
            "Defaults strip denoiser-internal variance/guidance channels."
        ),
    )

    beauty_rename_map_raw: list[ChannelRenamePair] = SettingsField(
        default_factory=lambda: [
            ChannelRenamePair(source="beauty.r", target="R"),
            ChannelRenamePair(source="beauty.g", target="G"),
            ChannelRenamePair(source="beauty.b", target="B"),
            ChannelRenamePair(source="a.Z", target="A"),
        ],
        title="Beauty Rename Map (raw pass-through)",
        description=(
            "Rename applied when denoise did not run and the combine job "
            "runs in pass-through mode over the raw render. When denoise "
            "ran, the rename map comes from the active denoiser backend "
            "instead (see the Denoising section)."
        ),
    )

    oiiotool_extra_args: str = SettingsField(
        "",
        title="Extra oiiotool Args",
        description=(
            "Raw string inserted verbatim into the oiiotool command after "
            "--compression and before -o. Useful for --planarconfig, "
            "--tile, --iconfig, etc."
        ),
    )

    output_compression: str = SettingsField(
        "zips",
        title="Output Compression",
        description=(
            "Passed to oiiotool as --compression <val>. Empty string "
            "disables the flag. Common values: 'zips' (fast, default), "
            "'zip', 'piz'."
        ),
    )

    output_data_type: str = SettingsField(
        "preserve",
        title="Output Data Type",
        description=(
            "'preserve' keeps oiiotool's default per-channel types (depth "
            "stays float, beauty stays half - required for Nuke). 'float' "
            "and 'half' force uniform output precision."
        ),
        enum_resolver=_output_data_type_enum,
    )

    write_manifest: bool = SettingsField(
        True,
        title="Write Combine Manifest",
        description=(
            "When True, the wrapper writes one <name>.combine.json sidecar "
            "per render sequence recording every channel decision. Useful "
            "for debugging; disable once the pipeline is stable."
        ),
    )

    verbose_logging: bool = SettingsField(
        True,
        title="Verbose Wrapper Logging",
        description=(
            "Toggles the wrapper script's -v flag. When True, channel "
            "lists and the full oiiotool command are logged to the "
            "Deadline task log."
        ),
    )

    output_subdirectory: str = SettingsField(
        "combined",
        title="Output Subdirectory",
        description="Subdirectory for combined output files.",
    )

    preserve_intermediates: bool = SettingsField(
        False,
        title="Preserve Intermediates",
        description="Keep intermediate files after processing.",
    )


class SharedToolsSettings(BaseSettingsModel):
    """Tools used by more than one step (denoise extraction AND combine)."""

    python_executable: str = SettingsField(
        "python",
        title="Python Executable (Deadline workers)",
        description=(
            "Python used on the Deadline workers to run ALL wrapper "
            "scripts (denoise and combine). Absolute path or a name "
            "resolvable on the worker PATH."
        ),
    )

    oiio_root_path: str = SettingsField(
        "/opt/oiio",
        title="OIIO Root Path",
        description=(
            "Path to the OpenImageIO installation root on the Deadline "
            "workers. Used by the combine step and by OIDN channel "
            "extraction."
        ),
    )

    oiio_exe: str = SettingsField(
        "oiiotool",
        title="oiiotool Executable Name",
        description=(
            "Name of the oiiotool executable in <OIIO root>/bin. Set to "
            "'oiiotool.exe' for Windows worker pools."
        ),
    )


class LumaDenoiseSettings(BaseSettingsModel):
    """Post-render denoise + combine pipeline for Houdini USD renders."""

    denoise: DenoiseSettings = SettingsField(
        default_factory=DenoiseSettings,
        title="Denoising",
        description="The denoise Deadline job (runs after the render job).",
    )

    combine: CombineSettings = SettingsField(
        default_factory=CombineSettings,
        title="OIIO Combine",
        description=(
            "The OIIO combine Deadline job (runs after the denoise job; "
            "merges denoised beauty with untouched AOVs)."
        ),
    )

    shared: SharedToolsSettings = SettingsField(
        default_factory=SharedToolsSettings,
        title="Shared Tools",
        description="Worker-side tools used by both steps.",
    )
```

- [ ] **Step 2:** `python -m py_compile server/settings.py` → exit 0. `python -m pytest tests -v` → 78 passed (client not yet re-pathed; tests don't touch settings.py).
- [ ] **Step 3:** Commit: `git add server/settings.py && git commit -m "feat(settings)!: restructure into denoise/combine/shared groups"`

---

### Task 2: Backends — nested paths + rename pairs (TDD)

**Files:** Modify `client/luma_denoise/denoisers/base.py`, `renderman.py`, `oidn.py`, `tests/test_denoiser_backends.py`

- [ ] **Step 1:** Update the test fixtures and add rename assertions in `tests/test_denoiser_backends.py`:

Replace `RM_SETTINGS` with:

```python
RM_SETTINGS = {
    "shared": {"python_executable": "/usr/bin/python3",
               "oiio_root_path": "/opt/oiio", "oiio_exe": "oiiotool"},
    "denoise": {
        "denoiser": "renderman",
        "renderman": {
            "rmantree_path": "/opt/pixar/RenderManProServer-26.3",
            "denoise_exe": "denoise_batch",
            "pixar_license": "9010@192.168.35.28",
            "tiled_denoise_threshold": 2048,
            "wrapper_script_path": "L:/scripts/{version}/renderman_denoise.py",
            "beauty_rename_map": [
                {"source": "Ci.r", "target": "R"},
                {"source": "a.Z", "target": "A"},
            ],
        },
    },
}
```

Replace `OIDN_SETTINGS` with:

```python
OIDN_SETTINGS = {
    "shared": {"python_executable": "/usr/bin/python3",
               "oiio_root_path": "/opt/oiio", "oiio_exe": "oiiotool"},
    "denoise": {
        "denoiser": "oidn",
        "oidn": {
            "oidn_root_path": "/opt/oidn",
            "denoise_exe": "oidnDenoise",
            "wrapper_script_path": "L:/scripts/{version}/oidn_denoise.py",
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

Update existing tests:
- `test_base_get_executable_uses_python_executable_setting`: first assert becomes `backend.get_executable({"shared": {"python_executable": "/py/bin/python3"}}) == "/py/bin/python3"`; the `{}` → `"python"` assert stays.
- `test_base_resolve_wrapper_path_substitutes_version`: settings become `{"denoise": {"renderman": {"wrapper_script_path": "L:/scripts/{version}/renderman_denoise.py"}}}`.
- `test_base_resolve_wrapper_path_empty_raises_actionable_error`: settings become `{"denoise": {"renderman": {"wrapper_script_path": ""}}}` and the match becomes `"denoise.renderman.wrapper_script_path"`.
- `test_renderman_validate_requires_wrapper_path`: `bad = {"denoise": {"renderman": {"wrapper_script_path": ""}}}`, match `"denoise.renderman.wrapper_script_path"`.
- `test_oidn_validate_requires_wrapper_path` / `test_oidn_validate_requires_guide_channels`: rebuild `bad` as a deep copy of `OIDN_SETTINGS` with the one field blanked, e.g.:

```python
def _oidn_settings_with(**overrides):
    import copy
    settings = copy.deepcopy(OIDN_SETTINGS)
    settings["denoise"]["oidn"].update(overrides)
    return settings
```

and use `_oidn_settings_with(wrapper_script_path="")` (match `"oidn.wrapper_script_path"`) / `_oidn_settings_with(albedo_channel="")` (match `"albedo_channel"`).

Add new assertions to `test_renderman_arguments_basic`:

```python
    assert "--rename Ci.r=R" in args
    assert "--rename a.Z=A" in args
```

Add new assertions to `test_oidn_arguments`:

```python
    assert "--rename beauty.r=R" in args
    assert "--rename a.Z=A" in args
```

Add one new test:

```python
def test_rename_pair_args_skips_incomplete_pairs():
    backend = RendermanDenoiser()
    settings = {"denoise": {"renderman": {"beauty_rename_map": [
        {"source": "Ci.r", "target": "R"},
        {"source": "", "target": "X"},
        {"source": "Y", "target": ""},
    ]}}}
    assert backend.rename_pair_args(settings) == ["--rename", "Ci.r=R"]
```

- [ ] **Step 2:** Run `python -m pytest tests/test_denoiser_backends.py -v` → failures (old paths).
- [ ] **Step 3:** Update `base.py`:

```python
    def get_executable(self, settings: dict) -> str:
        """Executable for the Deadline job — the worker Python.

        Both current backends run Python wrapper scripts, so this is the
        same ``shared.python_executable`` setting the combine wrapper uses.
        """
        return (settings.get("shared", {}) or {}).get(
            "python_executable", "python")
```

```python
    def _backend_settings(self, settings: dict) -> dict:
        denoise_settings = settings.get("denoise", {}) or {}
        return denoise_settings.get(self.name, {}) or {}
```

In `_resolve_wrapper_path`, the error message's field reference becomes `f"luma-denoise: 'denoise.{self.name}.wrapper_script_path' is not "`.

Add to `DenoiserBackend` (after `_resolve_wrapper_path`):

```python
    def rename_pair_args(self, settings: dict) -> list:
        """Backend's beauty_rename_map as ['--rename', 'SRC=DST', ...].

        The wrapper records these pairs verbatim in the denoise manifest's
        beauty_channel_map; the combine step consumes them from there.
        """
        args = []
        pairs = self._backend_settings(settings).get(
            "beauty_rename_map", []) or []
        for pair in pairs:
            if isinstance(pair, dict):
                source = pair.get("source", "")
                target = pair.get("target", "")
            else:
                source = getattr(pair, "source", "")
                target = getattr(pair, "target", "")
            if source and target:
                args.extend(["--rename", quote(f"{source}={target}")])
        return args
```

- [ ] **Step 4:** Update `renderman.py` `get_arguments`: no path-construction changes (its config already comes from `_backend_settings`), but insert `parts.extend(self.rename_pair_args(settings))` immediately BEFORE `parts.append("--verbose")`.
- [ ] **Step 5:** Update `oidn.py` `get_arguments`: replace the two top-level reads

```python
        oiio_root = settings.get("oiio_root_path", "/opt/oiio")
        oiio_exe = settings.get("oiio_exe", "oiiotool")
```

with

```python
        shared = settings.get("shared", {}) or {}
        oiio_root = shared.get("oiio_root_path", "/opt/oiio")
        oiio_exe = shared.get("oiio_exe", "oiiotool")
```

and insert `parts.extend(self.rename_pair_args(settings))` immediately BEFORE the `"--verbose"` entry (restructure the list build so `--verbose` is appended after the extend, mirroring renderman.py).

In `oidn.py` `validate`, the per-field error message keeps naming `'oidn.{field}'` (unchanged).

- [ ] **Step 6:** `python -m pytest tests/test_denoiser_backends.py -v` → all pass (20 tests). Full suite → 79 passed.
- [ ] **Step 7:** Commit: `git add client/luma_denoise/denoisers tests/test_denoiser_backends.py && git commit -m "feat(denoisers): nested settings paths and per-backend rename pairs"`

---

### Task 3: Wrappers — `--rename` support (TDD)

**Files:** Modify `client/luma_denoise/scripts/renderman_denoise.py`, `oidn_denoise.py`, `tests/test_renderman_denoise.py`, `tests/test_oidn_denoise.py`

- [ ] **Step 1:** Append tests.

To `tests/test_renderman_denoise.py`:

```python
def test_build_manifest_rename_pairs_override_default():
    args = _args(extra=["--rename", "Ci.r=red", "--rename", "a.Z=alpha"])
    manifest = renderman_denoise.build_manifest(args)
    assert manifest["beauty_channel_map"] == {"Ci.r": "red", "a.Z": "alpha"}


def test_main_malformed_rename_fails_before_denoise(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        renderman_denoise.subprocess, "run",
        lambda *a, **k: calls.append(a))
    rc = renderman_denoise.main([
        "--denoise-exe", "/opt/pixar/bin/denoise_batch",
        "--input", str(tmp_path / "shot_main.1001.exr"),
        "--output-dir", str(tmp_path / "denoised"),
        "--frame-start", "1001",
        "--frame-end", "1100",
        "--rename", "no-equals-sign",
    ])
    assert rc == 1
    assert calls == []
```

To `tests/test_oidn_denoise.py`:

```python
def test_build_manifest_rename_pairs_override_derivation(tmp_path):
    argv = _argv(tmp_path) + ["--rename", "beauty.r=red", "--rename", "a.Z=alpha"]
    args = oidn_denoise.parse_args(argv)
    manifest = oidn_denoise.build_manifest(
        args, ["beauty.r", "beauty.g", "beauty.b"])
    assert manifest["beauty_channel_map"] == {"beauty.r": "red", "a.Z": "alpha"}


def test_main_malformed_rename_fails_before_work(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(
        oidn_denoise.subprocess, "run", lambda *a, **k: calls.append(a))
    rc = oidn_denoise.main(_argv(tmp_path) + ["--rename", "bad"])
    assert rc == 1
    assert calls == []
    assert "rename" in capsys.readouterr().err
```

- [ ] **Step 2:** Run both test files → new tests fail (`unrecognized arguments: --rename`).
- [ ] **Step 3:** `renderman_denoise.py`:
  - Add to `parse_args`:

```python
    parser.add_argument("--rename", action="append", default=[],
                        metavar="SRC=DST",
                        help="Beauty channel rename pair recorded in the "
                             "manifest. May be given multiple times. "
                             "Default: the built-in RenderMan Ci map.")
```

  - Add module function (after `_strip_frame_token`):

```python
def parse_rename_pairs(pairs: list) -> dict:
    """Parse ['src=dst', ...] into a dict; raises ValueError on bad pairs."""
    out = {}
    for pair in pairs:
        if "=" not in pair:
            raise ValueError(
                f"Malformed rename pair (expected 'src=dst'): {pair}")
        src, dst = pair.split("=", 1)
        src, dst = src.strip(), dst.strip()
        if not src or not dst:
            raise ValueError(f"Empty source or target in rename pair: {pair}")
        out[src] = dst
    return out
```

  - In `build_manifest`, replace `"beauty_channel_map": dict(RENDERMAN_BEAUTY_MAP),` with:

```python
        "beauty_channel_map": (parse_rename_pairs(args.rename)
                               if args.rename
                               else dict(RENDERMAN_BEAUTY_MAP)),
```

  - In `main`, validate pairs BEFORE running denoise — insert right after `args = parse_args(argv)`:

```python
    try:
        parse_rename_pairs(args.rename)
    except ValueError as exc:
        print(f"[renderman_denoise] ERROR: {exc}", file=sys.stderr)
        return 1
```

- [ ] **Step 4:** `oidn_denoise.py`:
  - Add the same `--rename` argument to `parse_args` (help text: "Default: derived from the beauty layer channels plus a.Z->A.").
  - Add the same `parse_rename_pairs` function (after `_strip_frame_token`).
  - In `build_manifest`, replace the `beauty_map` construction block with:

```python
    if args.rename:
        beauty_map = parse_rename_pairs(args.rename)
    else:
        beauty_map = {}
        if len(beauty_channels) >= 3:
            beauty_map = {
                beauty_channels[0]: "R",
                beauty_channels[1]: "G",
                beauty_channels[2]: "B",
            }
        beauty_map.update(DEFAULT_ALPHA_RENAME)
```

  - In `_run`, validate pairs first — insert as the FIRST statement (before the frame-range guard):

```python
    parse_rename_pairs(args.rename)
```

  - In `main`, widen the except clause: `except (RuntimeError, ValueError) as exc:`.
- [ ] **Step 5:** `python -m pytest tests/test_renderman_denoise.py tests/test_oidn_denoise.py -v` → all pass (9 + 14). Full suite → 83 passed.
- [ ] **Step 6:** Commit: `git add client/luma_denoise/scripts/renderman_denoise.py client/luma_denoise/scripts/oidn_denoise.py tests/test_renderman_denoise.py tests/test_oidn_denoise.py && git commit -m "feat(scripts): --rename pairs drive the denoise manifest beauty map"`

---

### Task 4: `LumaDenoiseUsdRender` — nested settings reads

**Files:** Modify `client/luma_denoise/plugins/publish/houdini/luma_denoise_publish.py`

No local tests (AYON imports unavailable). READ the file first.

- [ ] **Step 1:** In `get_job_info`, the settings block currently reads flat keys. Replace:

```python
        project_settings = context.data["project_settings"]
        denoise_settings = project_settings["luma-denoise"]
```

with:

```python
        project_settings = context.data["project_settings"]
        denoise_settings = project_settings["luma-denoise"].get("denoise", {})
```

and re-key the three job fields: `denoise_settings.get("priority", 50)`, `denoise_settings.get("pool", "luma")`, `denoise_settings.get("group", "denoise_group")` (replacing `denoise_deadline_priority`/`denoise_pool`/`denoise_group`).

- [ ] **Step 2:** In `get_denoise_enabled`, replace the body's settings access:

```python
        project_settings = instance.context.data["project_settings"]
        denoise_settings = project_settings["luma-denoise"].get("denoise", {})

        if not denoise_settings.get("enabled", False):
            self.log.info("Denoising disabled in 'luma-denoise' settings.")
            return False

        default_enabled = denoise_settings.get("enabled", True)
```

(keep the rest of the method unchanged).

- [ ] **Step 3:** In `process`, the backend-resolution block changes one line — `backend_name` now comes from the nested group:

```python
            backend_name = denoise_settings.get(
                "denoise", {}).get("denoiser", "renderman")
```

where `denoise_settings` in that block is the FULL addon dict (`project_settings["luma-denoise"]`) — verify the block still passes the FULL dict to `self._backend.validate(...)` and `self._backend.get_environment(...)`; rename the local variable to `addon_settings` for clarity:

```python
            # Resolve the denoiser backend from settings and validate config.
            project_settings = context.data["project_settings"]
            addon_settings = project_settings["luma-denoise"]
            backend_name = addon_settings.get(
                "denoise", {}).get("denoiser", "renderman")
            self._backend = get_denoiser_backend(backend_name)
            self._backend.validate(instance, addon_settings)
            instance.data["denoise_backend"] = self._backend.name
            self.log.info(f"Using denoiser backend: {self._backend.name}")
```

and the env loop becomes `self._backend.get_environment(addon_settings).items()`.

- [ ] **Step 4:** In `get_plugin_info`, the dict passed to the backend stays the FULL addon dict — verify it reads `instance.context.data["project_settings"]["luma-denoise"]` (no `.get("denoise")`); rename its local from `denoise_settings` to `addon_settings` for clarity.
- [ ] **Step 5:** Verify: `python -m py_compile client/luma_denoise/plugins/publish/houdini/luma_denoise_publish.py`; `git grep -n "denoise_deadline_priority\|denoise_pool\|denoise_group\|denoise_enabled" -- client` → no hits in this file; full suite → 83 passed.
- [ ] **Step 6:** Commit: `git add client/luma_denoise/plugins/publish/houdini/luma_denoise_publish.py && git commit -m "refactor(publish): read nested denoise settings group"`

---

### Task 5: `ExtractOiioCombine` — nested reads + active-backend fallback map

**Files:** Modify `client/luma_denoise/plugins/publish/houdini/extract_oiio_combine.py`

READ the file first. All changes are in `get_job_info`, `get_plugin_info`, and `process`.

- [ ] **Step 1:** `get_job_info`: re-key the job fields to the combine group. Where it reads the addon settings, derive `combine_settings = project_settings["luma-denoise"].get("combine", {})` and use `combine_settings.get("priority", 50)`, `.get("pool", "default")`, `.get("group", "default")` (replacing `combine_deadline_priority`/`combine_pool`/`combine_group`).
- [ ] **Step 2:** `get_plugin_info`: at the top, after `oiio_settings = project_settings.get("luma-denoise", {})`, add:

```python
        combine_settings = oiio_settings.get("combine", {}) or {}
        shared_settings = oiio_settings.get("shared", {}) or {}
```

Then re-path every read:
- `oiio_root` → `shared_settings.get("oiio_root_path", <keep the existing long default>)`
- `oiiotool_path` → `os.path.join(oiio_root, "bin", shared_settings.get("oiio_exe", "oiiotool")).replace("\\", "/")` (the hardcoded `"oiiotool.exe"` suffix is REMOVED — Windows pools set `shared.oiio_exe` to `oiiotool.exe`)
- `output_subdirectory` → `combine_settings.get("output_subdirectory", "combined")`
- rename-pair selection — replace the current `if denoise_ran: ... beauty_rename_map_denoised ... else: ... beauty_rename_map_raw ...` block with:

```python
        if denoise_ran:
            denoised_path = f"{dirname}/denoised/{shot_name}.<STARTFRAME%4>.{extension}"
            # Fallback rename map (used only when no denoise manifest is
            # found): the ACTIVE backend's map, so it always matches the
            # denoiser that actually ran.
            backend_name = (instance.data.get("denoise_backend")
                            or oiio_settings.get("denoise", {}).get(
                                "denoiser", "renderman"))
            backend_settings = oiio_settings.get("denoise", {}).get(
                backend_name, {}) or {}
            rename_pairs_cfg = backend_settings.get(
                "beauty_rename_map", DEFAULT_RENAME_DENOISED)
        else:
            denoised_path = renders_path
            rename_pairs_cfg = combine_settings.get(
                "beauty_rename_map_raw", DEFAULT_RENAME_RAW)
```

- `python_exe` → `shared_settings.get("python_executable", "python")`
- `wrapper_template` → `combine_settings.get("wrapper_script_path", "")`; in the RuntimeError message, change `'wrapper_script_path'` to `'combine.wrapper_script_path'`
- `user_excludes` → `combine_settings.get("channel_exclude_patterns", default_excludes)`
- `extra_args`/`compression`/`data_type` → `combine_settings.get("oiiotool_extra_args", "")` / `.get("output_compression", "zips")` / `.get("output_data_type", "preserve")`
- `write_manifest` → `combine_settings.get("write_manifest", True)` (key renamed from `write_combine_manifest`)
- `verbose` → `combine_settings.get("verbose_logging", True)` (key renamed from `wrapper_verbose_logging`)

- [ ] **Step 3:** `process`: re-path three reads:
- `if not oiio_settings.get("oiio_enabled", True):` → `if not oiio_settings.get("combine", {}).get("enabled", True):`
- `run_when_no_denoise = oiio_settings.get("run_when_denoise_disabled", False)` → `... = oiio_settings.get("combine", {}).get("run_when_denoise_disabled", False)`
- `instance.data["stagingDir"] = oiio_settings.get("output_subdirectory", "combined")` → `... = oiio_settings.get("combine", {}).get("output_subdirectory", "combined")`

- [ ] **Step 4:** Verify: `python -m py_compile client/luma_denoise/plugins/publish/houdini/extract_oiio_combine.py`; `git grep -n "beauty_rename_map_denoised\|combine_deadline_priority\|oiio_enabled\|write_combine_manifest\|wrapper_verbose_logging" -- client server` → NO hits; full suite → 83 passed.
- [ ] **Step 5:** Commit: `git add client/luma_denoise/plugins/publish/houdini/extract_oiio_combine.py && git commit -m "refactor(publish): combine reads nested groups, fallback map from active backend"`

---

### Task 6: Version 0.3.0, CLAUDE.md, full verification

**Files:** Modify `package.py`, `CLAUDE.md`

- [ ] **Step 1:** `package.py`: `version = "0.2.0"` → `version = "0.3.0"`.
- [ ] **Step 2:** `CLAUDE.md`:
  - In "Server Side", replace the `settings.py` bullet's description with: `settings.py — Pydantic settings model with three groups: Denoising (denoiser dropdown + per-backend config incl. beauty rename maps), OIIO Combine (combine job + pass-through rename map), Shared Tools (worker python, OIIO paths).`
  - In "Denoiser Backends", replace `(falling back to the `beauty_rename_map_denoised` setting when absent)` with `(falling back to the active backend's `beauty_rename_map` setting when absent)`.
- [ ] **Step 3:** Full verify: `python -m pytest tests -v` → 83 passed; `python create_package.py --skip-zip` → exit 0, version 0.3.0; `git status` → only intended files (+ regenerated `client/luma_denoise/version.py` — include it in the commit).
- [ ] **Step 4:** Commit: `git add package.py CLAUDE.md client/luma_denoise/version.py && git commit -m "build: bump version to 0.3.0, document settings groups"`

---

## Post-Implementation (after merge)

Build + upload to the AYON server + restart (same procedure as 0.2.0), then
enter settings once in the new layout. Windows combine pools: set
`shared.oiio_exe = "oiiotool.exe"`.
