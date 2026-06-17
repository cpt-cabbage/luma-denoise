# Worker-Side Path Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move tool-path resolution from submit-time (per-step platform dropdown) to runtime (the wrapper picks by its own OS). Wrapper/python/library paths become single values handled by Deadline Path Mapping; tool install roots stay per-OS but are resolved on the worker. Delete both Worker Platform dropdowns. Version 0.5.0.

**Spec:** `docs/superpowers/specs/2026-06-15-worker-side-resolution-design.md`

**Key compatibility rule for wrappers:** each wrapper KEEPS its existing resolved-path flag (`--denoise-exe`, `--oidn-exe`/`--oiiotool`) as an OPTIONAL fallback, and ADDS the per-OS root flags. Resolution precedence at runtime: if the current platform's root is non-empty → build `<root>/bin/<exe-name>` (append `.exe` on Windows); else fall back to the legacy flag; else fail naming the platform. This keeps existing wrapper unit tests green while adding the new path.

Suite currently: 86 passed.

---

### Task 1: settings.py — revert to single values, keep 3 per-OS roots, fix labels

**File:** `server/settings.py`

- [ ] **Step 1:** Remove `_layout = "expanded"` from `MultiplatformPathModel` (so the parent field title renders). Keep its windows/linux/darwin fields.
- [ ] **Step 2:** Remove the `worker_platform` field from BOTH `DenoiseSettings` and `CombineSettings` (and `_platform_enum` if now unused — it is; delete it).
- [ ] **Step 3:** Convert these fields from `MultiplatformPathModel` BACK to `str` (single value), keeping their existing titles/descriptions but dropping the per-platform framing:
  - `RendermanDenoiserSettings.denoise_exe` → `str` default `"denoise_batch"`, title "Denoiser Executable Name", desc "Name of the RenderMan denoiser executable in <RMANTREE>/bin (the wrapper appends .exe on Windows)."
  - `RendermanDenoiserSettings.wrapper_script_path` → `str` default `""`, title unchanged, desc: "Absolute path to renderman_denoise.py on the shared library. Single value — Deadline Path Mapping translates it per worker OS. Supports the {version} token. MUST be set."
  - `OidnDenoiserSettings.denoise_exe` → `str` default `"oidnDenoise"`, analogous desc.
  - `OidnDenoiserSettings.wrapper_script_path` → `str` default `""`, analogous to renderman wrapper desc but oidn_denoise.py.
  - `CombineSettings.wrapper_script_path` → `str` default `""`, analogous (oiio_combine.py).
  - `SharedToolsSettings.python_executable` → `str` default `"python"`, desc: "Python that Deadline launches for ALL wrapper scripts. Single value — must resolve on every worker's PATH or be a Path-Mapped absolute path."
  - `SharedToolsSettings.oiio_exe` → `str` default `"oiiotool"`, desc "Name of the oiiotool executable in <OIIO root>/bin (wrapper appends .exe on Windows)."
- [ ] **Step 4:** KEEP these three as `MultiplatformPathModel` (per-OS, unmapped installs) with their existing default_factory triplets:
  - `RendermanDenoiserSettings.rmantree_path`
  - `OidnDenoiserSettings.oidn_root_path`
  - `SharedToolsSettings.oiio_root_path`
  Update their descriptions to add: "Per worker OS — resolved on the worker at runtime (these installs aren't in Deadline Path Mapping)."
- [ ] **Step 5:** Verify `python -m py_compile server/settings.py` → 0; `python -m pytest tests -q` → 86 passed; `git grep -n "worker_platform\|_layout = \"expanded\"" -- server` → no hits.
- [ ] **Step 6:** Commit: `feat(settings)!: worker-side resolution — single-value wrappers, per-OS tool roots`

---

### Task 2: base.py + backends — emit per-OS root triplets, drop submit-time resolution (TDD)

**Files:** `client/luma_denoise/denoisers/base.py`, `renderman.py`, `oidn.py`, `tests/test_denoiser_backends.py`

**base.py changes:**
- Remove `_worker_platform`.
- `get_executable` → plain single value:
```python
    def get_executable(self, settings: dict) -> str:
        shared = settings.get("shared", {}) or {}
        return shared.get("python_executable", "python") or "python"
```
- `_resolve_wrapper_path` → single value (no platform):
```python
    def _resolve_wrapper_path(self, settings: dict) -> str:
        template = self._backend_settings(settings).get("wrapper_script_path", "")
        if not template:
            raise RuntimeError(
                f"luma-denoise: 'denoise.{self.name}.wrapper_script_path' is "
                "not set. Point it at "
                f"{self.wrapper_filename or 'the wrapper script'} on the shared "
                "library (Deadline Path Mapping translates it per worker). "
                "Use the {version} token for per-version paths."
            )
        return template.replace("{version}", ADDON_VERSION)
```
- Keep `resolve_platform_value` (still used? no longer — but harmless to keep; KEEP it, it's tolerant and may be reused).
- Add a triplet-emitter helper:
```python
    @staticmethod
    def platform_triplet_args(prefix: str, value) -> list:
        """Emit ['--<prefix>-windows', w, '--<prefix>-linux', l,
        '--<prefix>-darwin', d] from a {windows,linux,darwin} dict (or a
        plain string applied to all three). Empty values are emitted as a
        literal '""' token so they survive command-line splitting."""
        if isinstance(value, dict):
            vals = {p: (value.get(p, "") or "") for p in
                    ("windows", "linux", "darwin")}
        else:
            v = value or ""
            vals = {"windows": v, "linux": v, "darwin": v}
        out = []
        for p in ("windows", "linux", "darwin"):
            v = vals[p]
            out.extend([f"--{prefix}-{p}", quote(v) if v else '""'])
        return out
```
- `rename_pair_args` unchanged.

**renderman.py `get_arguments`:** replace the `platform_key`/`rman_root`/`exe_name`/`denoise_exe` resolution block and the `--denoise-exe` arg with:
```python
        rm_settings = self._backend_settings(settings)
        # ... files/dirname/basename/frame_start/frame_end unchanged ...
        exe_name = rm_settings.get("denoise_exe", "denoise_batch") or "denoise_batch"
        pixar_license = rm_settings.get("pixar_license", "")
        wrapper_path = self._resolve_wrapper_path(settings)

        parts = [quote(wrapper_path)]
        parts.extend(self.platform_triplet_args(
            "rmantree", rm_settings.get("rmantree_path", "")))
        parts.extend(["--denoise-exe-name", quote(exe_name)])
        if pixar_license:
            parts.extend(["--pixar-license", quote(pixar_license)])
        parts.extend([
            "--input", quote(f"{dirname}/{basename}"),
            "--output-dir", quote(f"{dirname}/denoised"),
            "--frame-start", str(frame_start),
            "--frame-end", str(frame_end),
            "--addon-version", ADDON_VERSION,
        ])
        if self._frame_count(instance) >= 8:
            parts.append("--cross-frame")
        if self.detect_large_image(instance, rm_settings):
            parts.extend(["--tiles", "2", "2"])
        parts.extend(self.rename_pair_args(settings))
        parts.append("--verbose")
        return " ".join(parts)
```
**renderman.py `get_environment`:** return `{}` (RMANTREE/PATH/PIXAR_LICENSE_FILE now set by the wrapper at runtime):
```python
    def get_environment(self, settings: dict) -> dict:
        return {}
```
(Keep `detect_large_image`, `_frame_count`, `_count_custom_frames`, `_iter_render_products` unchanged. Remove the now-unused `resolve_platform_value` import if nothing else uses it.)

**oidn.py `get_arguments`:** replace the resolution block + `--oidn-exe`/`--oiiotool` args:
```python
        oidn_settings = self._backend_settings(settings)
        shared = settings.get("shared", {}) or {}
        # ... files/dirname/basename/frame_start/frame_end unchanged ...
        oidn_exe_name = oidn_settings.get("denoise_exe", "oidnDenoise") or "oidnDenoise"
        oiio_exe_name = shared.get("oiio_exe", "oiiotool") or "oiiotool"
        wrapper_path = self._resolve_wrapper_path(settings)

        parts = [quote(wrapper_path)]
        parts.extend(self.platform_triplet_args(
            "oidn-root", oidn_settings.get("oidn_root_path", "")))
        parts.extend(["--oidn-exe-name", quote(oidn_exe_name)])
        parts.extend(self.platform_triplet_args(
            "oiio-root", shared.get("oiio_root_path", "")))
        parts.extend(["--oiio-exe-name", quote(oiio_exe_name)])
        parts.extend([
            "--input", quote(f"{dirname}/{basename}"),
            "--output-dir", quote(f"{dirname}/denoised"),
            "--frame-start", str(frame_start),
            "--frame-end", str(frame_end),
            "--beauty-channel", quote(oidn_settings.get("beauty_channel", "beauty")),
            "--albedo-channel", quote(oidn_settings.get("albedo_channel", "albedo")),
            "--normal-channel", quote(oidn_settings.get("normal_channel", "N")),
            "--addon-version", ADDON_VERSION,
        ])
        parts.extend(self.rename_pair_args(settings))
        parts.append("--verbose")
        return " ".join(parts)
```
**oidn.py `get_environment`:** return `{}`. Keep `validate` (channel checks + wrapper path) unchanged. Remove unused `resolve_platform_value` import if unused.

**tests/test_denoiser_backends.py:** update fixtures and assertions:
- In `RM_SETTINGS["denoise"]` remove `worker_platform`; set `renderman.wrapper_script_path` to a plain string `"L:/scripts/{version}/renderman_denoise.py"`; set `renderman.denoise_exe` to `"denoise_batch"`; keep `renderman.rmantree_path` as the dict `{"windows": "C:/Pixar/RMP", "linux": "/opt/pixar/RenderManProServer-26.3", "darwin": ""}`; add `renderman.pixar_license` "9010@x". In `shared` set `python_executable` to plain `"/usr/bin/python3"`, keep `oiio_root_path` dict, `oiio_exe` plain `"oiiotool"`.
- Same shape for `OIDN_SETTINGS`: remove worker_platform; `oidn.wrapper_script_path` plain str; `oidn.denoise_exe` "oidnDenoise"; `oidn.oidn_root_path` dict `{"linux":"/opt/oidn","windows":"","darwin":""}`; channels; shared as above.
- Replace `test_base_get_executable_resolves_worker_platform` with:
```python
def test_base_get_executable_returns_python_executable():
    backend = base.DenoiserBackend()
    assert backend.get_executable(
        {"shared": {"python_executable": "/usr/bin/python3"}}) == "/usr/bin/python3"
    assert backend.get_executable({}) == "python"
```
- Update the two base wrapper-path tests to plain-string settings:
```python
def test_base_resolve_wrapper_path_substitutes_version():
    backend = base.DenoiserBackend(); backend.name = "renderman"
    backend.wrapper_filename = "renderman_denoise.py"
    out = backend._resolve_wrapper_path(
        {"denoise": {"renderman": {"wrapper_script_path": "L:/s/{version}/renderman_denoise.py"}}})
    assert "{version}" not in out and out.endswith("/renderman_denoise.py")

def test_base_resolve_wrapper_path_empty_raises():
    backend = base.DenoiserBackend(); backend.name = "renderman"
    with pytest.raises(RuntimeError, match="wrapper_script_path"):
        backend._resolve_wrapper_path({"denoise": {"renderman": {"wrapper_script_path": ""}}})
```
- Remove `test_base_get_executable_resolves_worker_platform`, `test_renderman_arguments_windows_worker_platform`, and `test_wrapper_path_missing_for_platform_names_platform` (platform-at-submit no longer exists). Replace the windows-platform test with a triplet-emission test:
```python
def test_platform_triplet_args_from_dict():
    out = base.DenoiserBackend.platform_triplet_args(
        "rmantree", {"windows": "C:/RMP", "linux": "/opt/rmp", "darwin": ""})
    assert out == ["--rmantree-windows", "C:/RMP",
                   "--rmantree-linux", "/opt/rmp",
                   "--rmantree-darwin", '""']

def test_platform_triplet_args_from_plain_string():
    out = base.DenoiserBackend.platform_triplet_args("oidn-root", "/opt/oidn")
    assert out == ["--oidn-root-windows", "/opt/oidn",
                   "--oidn-root-linux", "/opt/oidn",
                   "--oidn-root-darwin", "/opt/oidn"]
```
- Update `test_renderman_arguments_basic`: assert the new surface, e.g.
  `"--rmantree-linux /opt/pixar/RenderManProServer-26.3" in args`,
  `"--denoise-exe-name denoise_batch" in args`,
  `"--pixar-license 9010@x" in args` (use whatever license string the fixture sets; note "C:/Pixar/RMP" has no space but "C:/Program Files/..." would — the fixture uses the no-space form so no quoting),
  and that `"--denoise-exe " not in args` (old flag gone). Keep the input/output/frame/cross-frame assertions.
- Update `test_oidn_arguments`: assert `"--oidn-root-linux /opt/oidn"`, `"--oidn-exe-name oidnDenoise"`, `"--oiio-root-linux /opt/oiio"`, `"--oiio-exe-name oiiotool"`, channels, and that `"--oidn-exe "` / `"--oiiotool "` are gone.
- Update `test_renderman_environment` / `test_oidn_environment_*` to assert `get_environment(...) == {}`.
- Keep validate tests (they hit `_resolve_wrapper_path` and oidn channel checks) — adjust their settings to plain-string wrapper paths.

- [ ] **Steps:** write/adjust tests first → run (fail) → implement → `python -m pytest tests/test_denoiser_backends.py -v` green → full suite. Commit: `feat(denoisers): emit per-OS tool roots, drop submit-time resolution`

---

### Task 3: renderman_denoise.py — runtime root resolution + env (TDD)

**Files:** `client/luma_denoise/scripts/renderman_denoise.py`, `tests/test_renderman_denoise.py`

- [ ] Add helper functions (module level):
```python
import platform as _platform_mod

def current_platform() -> str:
    return {"Windows": "windows", "Linux": "linux",
            "Darwin": "darwin"}.get(_platform_mod.system(), "linux")

def build_tool_path(root: str, exe_name: str, plat: str) -> str:
    exe = exe_name
    if plat == "windows" and not exe.lower().endswith(".exe"):
        exe = exe + ".exe"
    return f"{root.rstrip('/')}/bin/{exe}"
```
- `parse_args`: make `--denoise-exe` optional (`default=""`, drop `required=True`); add `--rmantree-windows`/`--rmantree-linux`/`--rmantree-darwin` (dest `rmantree_windows` etc., default ""), `--denoise-exe-name` (default "denoise_batch"), `--pixar-license` (default "").
- Add resolver:
```python
def resolve_denoise_exe(args, plat=None):
    """(exe_path, rmantree_root) for the current platform.
    Prefers per-OS rmantree roots; falls back to --denoise-exe."""
    plat = plat or current_platform()
    root = {"windows": args.rmantree_windows, "linux": args.rmantree_linux,
            "darwin": args.rmantree_darwin}.get(plat, "")
    if root:
        return build_tool_path(root, args.denoise_exe_name, plat), root
    if args.denoise_exe:
        return args.denoise_exe, ""
    raise RuntimeError(
        f"renderman_denoise: no RenderMan root for platform '{plat}'. "
        "Set denoise.renderman.rmantree_path for this OS.")
```
- `build_denoise_argv(args, denoise_exe)`: take the resolved exe as a parameter (replace `args.denoise_exe` at argv[0] with it).
- `main`: resolve `(denoise_exe, root)`; build env =
```python
    env = os.environ.copy()
    if root:
        env["RMANTREE"] = root
        env["PATH"] = f"{root}/bin" + os.pathsep + env.get("PATH", "")
    if args.pixar_license:
        env["PIXAR_LICENSE_FILE"] = args.pixar_license
```
  pass `env=env` to `subprocess.run`. Resolve BEFORE running; on `RuntimeError` from resolver, print `[renderman_denoise] ERROR: ...` and return 1 (do this in main with try/except around the resolve, like the existing rename-pair guard).
- Update docstring usage block.
- Tests: keep existing tests passing (they pass `--denoise-exe /opt/pixar/bin/denoise_batch`; with the legacy fallback these still resolve). Add:
```python
def test_resolve_denoise_exe_prefers_rmantree(monkeypatch):
    monkeypatch.setattr(renderman_denoise, "current_platform", lambda: "linux")
    args = renderman_denoise.parse_args([
        "--input", "/r/s.1001.exr", "--output-dir", "/r/denoised",
        "--frame-start", "1001", "--frame-end", "1001",
        "--rmantree-linux", "/opt/pixar/RMP", "--denoise-exe-name", "denoise_batch"])
    exe, root = renderman_denoise.resolve_denoise_exe(args)
    assert exe == "/opt/pixar/RMP/bin/denoise_batch"
    assert root == "/opt/pixar/RMP"

def test_resolve_denoise_exe_windows_appends_exe(monkeypatch):
    monkeypatch.setattr(renderman_denoise, "current_platform", lambda: "windows")
    args = renderman_denoise.parse_args([
        "--input", "/r/s.1001.exr", "--output-dir", "/r/denoised",
        "--frame-start", "1001", "--frame-end", "1001",
        "--rmantree-windows", "C:/RMP", "--denoise-exe-name", "denoise_batch"])
    exe, _ = renderman_denoise.resolve_denoise_exe(args)
    assert exe == "C:/RMP/bin/denoise_batch.exe"

def test_resolve_denoise_exe_missing_root_raises(monkeypatch):
    monkeypatch.setattr(renderman_denoise, "current_platform", lambda: "darwin")
    args = renderman_denoise.parse_args([
        "--input", "/r/s.1001.exr", "--output-dir", "/r/denoised",
        "--frame-start", "1001", "--frame-end", "1001",
        "--rmantree-linux", "/opt/pixar/RMP", "--denoise-exe-name", "denoise_batch"])
    with pytest.raises(RuntimeError, match="darwin"):
        renderman_denoise.resolve_denoise_exe(args)

def test_main_sets_rmantree_env(tmp_path, monkeypatch):
    captured = {}
    class R: returncode = 0; stdout = ""; stderr = ""
    def fake_run(argv, **kw):
        captured["env"] = kw.get("env"); captured["argv"] = argv; return R()
    monkeypatch.setattr(renderman_denoise, "current_platform", lambda: "linux")
    monkeypatch.setattr(renderman_denoise.subprocess, "run", fake_run)
    rc = renderman_denoise.main([
        "--input", str(tmp_path/"s.1001.exr"), "--output-dir", str(tmp_path/"denoised"),
        "--frame-start", "1001", "--frame-end", "1001",
        "--rmantree-linux", "/opt/pixar/RMP", "--denoise-exe-name", "denoise_batch",
        "--pixar-license", "9010@h"])
    assert rc == 0
    assert captured["argv"][0] == "/opt/pixar/RMP/bin/denoise_batch"
    assert captured["env"]["RMANTREE"] == "/opt/pixar/RMP"
    assert captured["env"]["PIXAR_LICENSE_FILE"] == "9010@h"
    assert captured["env"]["PATH"].startswith("/opt/pixar/RMP/bin")
```
  (add `import pytest` to that test file if missing). Need to make existing legacy-flag tests still pass: they pass `--denoise-exe <path>` and no rmantree → resolver returns the legacy path, root="" → no RMANTREE env. Those tests don't assert env, so they stay green; but `test_main_propagates_exit_code_and_writes_manifest` calls subprocess.run via monkeypatch — confirm the fake still works with the new `env=` kwarg (the fakes accept **kwargs). Verify build_denoise_argv now needs the exe param — update its existing direct test `test_build_denoise_argv_basic` to pass the exe: `build_denoise_argv(_args(), "/opt/pixar/bin/denoise_batch")` and keep the expected argv[0].
- Commit: `feat(renderman_denoise): resolve RenderMan root on the worker, set env at runtime`

---

### Task 4: oidn_denoise.py — runtime root resolution (TDD)

**Files:** `client/luma_denoise/scripts/oidn_denoise.py`, `tests/test_oidn_denoise.py`

- [ ] Add the same `current_platform()` + `build_tool_path()` helpers.
- `parse_args`: make `--oidn-exe` and `--oiiotool` optional (default ""); add `--oidn-root-windows/linux/darwin` (default ""), `--oidn-exe-name` (default "oidnDenoise"), `--oiio-root-windows/linux/darwin` (default ""), `--oiio-exe-name` (default "oiiotool").
- Add resolver used at the top of `_run` (after the rename/frame guards):
```python
def _pick_root(args, prefix, plat):
    return {"windows": getattr(args, f"{prefix}_windows"),
            "linux": getattr(args, f"{prefix}_linux"),
            "darwin": getattr(args, f"{prefix}_darwin")}.get(plat, "")

def resolve_tools(args, plat=None):
    plat = plat or current_platform()
    oidn_root = _pick_root(args, "oidn_root", plat)
    oiio_root = _pick_root(args, "oiio_root", plat)
    oidn_exe = (build_tool_path(oidn_root, args.oidn_exe_name, plat)
                if oidn_root else args.oidn_exe)
    oiiotool = (build_tool_path(oiio_root, args.oiio_exe_name, plat)
                if oiio_root else args.oiiotool)
    if not oidn_exe:
        raise RuntimeError(
            f"oidn_denoise: no OIDN root for platform '{plat}'.")
    if not oiiotool:
        raise RuntimeError(
            f"oidn_denoise: no OIIO root for platform '{plat}'.")
    return oidn_exe, oiiotool, oidn_root
```
- In `_run`: after the guards, `oidn_exe, oiiotool, oidn_root = resolve_tools(args)`, then `args.oidn_exe = oidn_exe; args.oiiotool = oiiotool` (so `build_frame_commands` / `read_channels` keep working unchanged). Build an env that prepends `<oidn_root>/bin` to PATH (when oidn_root set) and pass it to `_run_commands`; update `_run_commands(cmds, verbose, env=None)` to `subprocess.run(argv, env=env, ...)`.
- `main` already catches `(RuntimeError, ValueError)` → prints + returns 1, so a missing-root failure surfaces correctly.
- Tests: existing tests pass `--oidn-exe`/`--oiiotool` → legacy fallback keeps them green (the resolver returns those when roots absent). Add:
```python
def test_resolve_tools_prefers_roots(monkeypatch):
    monkeypatch.setattr(oidn_denoise, "current_platform", lambda: "windows")
    args = oidn_denoise.parse_args(_argv(Path("/r")) + [
        "--oidn-root-windows", "C:/oidn", "--oiio-root-windows", "C:/oiio"])
    oidn_exe, oiiotool, root = oidn_denoise.resolve_tools(args)
    assert oidn_exe == "C:/oidn/bin/oidnDenoise.exe"
    assert oiiotool == "C:/oiio/bin/oiiotool.exe"
    assert root == "C:/oidn"

def test_resolve_tools_missing_oidn_root_raises(monkeypatch):
    monkeypatch.setattr(oidn_denoise, "current_platform", lambda: "darwin")
    args = oidn_denoise.parse_args(_argv(Path("/r")) + [
        "--oiio-root-darwin", "/opt/oiio"])  # no oidn root, no legacy --oidn-exe
    with pytest.raises(RuntimeError, match="OIDN root"):
        oidn_denoise.resolve_tools(args)
```
  Note: `_argv` currently includes `--oidn-exe`/`--oiiotool`; for the two new tests build argv WITHOUT those (so the resolver must use roots / fail). Add a variant or inline argv lists omitting the legacy flags. Existing `_argv`-based tests keep the legacy flags and stay green. Also confirm `test_main_runs_all_frames_and_writes_manifest` (mradd: monkeypatch current_platform if needed) still works — it passes legacy `--oidn-exe`/`--oiiotool` via `_argv`, roots absent → resolver returns legacy values → unchanged behavior; the new `env=` kwarg on `_run_commands` must be accepted by its fake (the test monkeypatches `subprocess.run` with `**kw`). Verify.
- Commit: `feat(oidn_denoise): resolve OIDN/OIIO roots on the worker`

---

### Task 5: oiio_combine.py + combine plugin — runtime oiiotool resolution

**Files:** `client/luma_denoise/scripts/oiio_combine.py`, `client/luma_denoise/plugins/publish/houdini/extract_oiio_combine.py`, `tests/test_oiio_combine.py`

**oiio_combine.py:**
- Add `current_platform()` + `build_tool_path()` helpers (same as the others).
- `parse_args`: make `--oiiotool` optional (`default=""`, drop required); add `--oiio-root-windows/linux/darwin` (default ""), `--oiio-exe-name` (default "oiiotool").
- In `_run` (or at top of `main` before `_run`): resolve
```python
    plat = current_platform()
    root = {"windows": args.oiio_root_windows, "linux": args.oiio_root_linux,
            "darwin": args.oiio_root_darwin}.get(plat, "")
    oiiotool = build_tool_path(root, args.oiio_exe_name, plat) if root else args.oiiotool
    if not oiiotool:
        raise RuntimeError(
            f"oiio_combine: no OIIO root for platform '{plat}'.")
    args.oiiotool = oiiotool
```
  Place this at the very top of `_run` (after computing `pass_through` is fine; before any `read_channels`). The existing `main` wraps `_run` in `except (RuntimeError, ValueError)` → returns 1, so a missing root surfaces cleanly.
- Tests: existing tests pass `--oiiotool /bin/oiiotool` → with the legacy fallback they stay green (root absent → uses `args.oiiotool`). Add:
```python
def test_resolve_oiiotool_from_roots(monkeypatch):
    monkeypatch.setattr(oiio_combine, "current_platform", lambda: "windows")
    args = oiio_combine.parse_args([
        "--denoised", "/a/d.1001.exr", "--raw", "/a/r.1001.exr",
        "--output", "/a/o.1001.exr",
        "--oiio-root-windows", "C:/oiio", "--oiio-exe-name", "oiiotool"])
    # exercise the resolver indirectly: build_tool_path
    assert oiio_combine.build_tool_path("C:/oiio", "oiiotool", "windows") == "C:/oiio/bin/oiiotool.exe"
```
  (Keep it light — the heavy combine logic is already covered. One test for build_tool_path + one that `--oiiotool` still parses optionally.)

**extract_oiio_combine.py `get_plugin_info`:**
- Remove `worker_platform`. Replace the `oiio_root`/`oiiotool_path` block: instead of resolving, emit per-OS roots to the wrapper. Remove the `--oiiotool` part. Compute:
```python
        oiio_root_value = shared_settings.get("oiio_root_path", "")
        oiio_exe_name = shared_settings.get("oiio_exe", "oiiotool") or "oiiotool"
```
- `python_exe` → `shared_settings.get("python_executable", "python") or "python"` (single value, no resolve).
- `wrapper_template` → `combine_settings.get("wrapper_script_path", "")` (single value); keep the empty-check RuntimeError but simplify the message (drop the platform phrasing): "...'combine.wrapper_script_path' is not set. Point it at oiio_combine.py on the shared library...".
- In the `parts` build, REPLACE `parts.extend(["--oiiotool", self._quote(oiiotool_path)])` with per-OS roots + exe name. Add a tiny local emitter mirroring the backend's (the plugin can't import from denoisers cleanly at runtime? it already imports `resolve_platform_value` from denoisers.base — so import the triplet helper too):
  - Add import: `from luma_denoise.denoisers.base import resolve_platform_value, DenoiserBackend` (or a standalone triplet function). Simplest: reuse `DenoiserBackend.platform_triplet_args`:
```python
        parts.extend(DenoiserBackend.platform_triplet_args(
            "oiio-root", oiio_root_value))
        parts.extend(["--oiio-exe-name", self._quote(oiio_exe_name)])
```
  but `platform_triplet_args` uses `quote` (its own) not `self._quote`; that's fine — both quote-on-space; the empty `'""'` handling is what matters. Keep it.
- Remove the now-unused `resolve_platform_value` import if nothing else uses it (the rename-map block does NOT use it). Verify.
- Verify: `python -m py_compile` both files; `git grep -n "worker_platform\|--oiiotool\b" -- client/luma_denoise/plugins` → no hits; full suite green.
- Commit: `refactor(combine): resolve oiiotool on the worker from per-OS roots`

---

### Task 6: version 0.5.0 + docs + verify + build

**Files:** `package.py`, `CLAUDE.md`

- [ ] `package.py`: 0.4.0 → 0.5.0.
- [ ] `CLAUDE.md` settings.py bullet: replace the per-platform sentence with: "Tool install roots (RenderMan/OIDN/OIIO) are per-OS and resolved on the worker at runtime; wrapper/python/library paths are single values handled by Deadline Path Mapping. No submit-time platform choice."
- [ ] Full verify: `python -m pytest tests -v` (all green); `python create_package.py --skip-zip` exit 0, version 0.5.0; include regenerated `client/luma_denoise/version.py`.
- [ ] Commit: `build: bump version to 0.5.0, document worker-side resolution`

---

## Post-implementation (after merge)

Build + upload + restart (same as prior releases). Configure once: fill the
three per-OS install roots for RenderMan/OIDN/OIIO; set the wrapper script
paths, python, and exe names as single values; no platform dropdown.
