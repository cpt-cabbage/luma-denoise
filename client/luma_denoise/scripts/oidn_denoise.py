"""oidn_denoise.py - Deadline-worker wrapper for Intel Open Image Denoise.

OIDN is a color-buffer denoiser: its oidnDenoise CLI takes one 3-channel
image file plus albedo/normal guides as SEPARATE files, and cannot select
channels out of a packed multi-channel render EXR. This wrapper bridges that
gap per frame:

    1. read the raw EXR channel list
    2. hard-fail if the beauty/albedo/normal layers are missing (guides are
       REQUIRED for quality; this is a deliberate pipeline policy)
    3. extract each layer to a temp single-layer EXR via oiiotool
    4. run oidnDenoise --hdr beauty --alb albedo --nrm normal
    5. rename the denoised channels back to the original beauty names and
       write the frame into the denoised output directory

After the last frame a sequence-level <name>.denoise.json sidecar is written
describing the output channel naming; oiio_combine.py reads it downstream.

Standalone by design: deploy this single file to a shared filesystem (the
'shared.scripts_directory' luma-denoise setting points at the folder that
holds it). The channel reading block mirrors oiio_combine.py on purpose -
wrappers do not import each other.

Usage (per-OS roots, preferred):
    python oidn_denoise.py
        --oidn-root-linux /opt/oidn  --oidn-root-windows C:/oidn --oidn-root-darwin ""
        --oidn-exe-name oidnDenoise
        --oiio-root-linux /opt/oiio  --oiio-root-windows C:/oiio --oiio-root-darwin ""
        --oiio-exe-name oiiotool
        --input <first-frame.exr> --output-dir <dir>
        --frame-start N --frame-end N
        --beauty-channel beauty --albedo-channel albedo --normal-channel N
        [--addon-version V] [--keep-temps] [--verbose]

Usage (legacy resolved paths, kept for backwards compat):
    python oidn_denoise.py
        --oidn-exe <path> --oiiotool <path>
        --input <first-frame.exr> --output-dir <dir>
        --frame-start N --frame-end N
        --beauty-channel beauty --albedo-channel albedo --normal-channel N
        [--addon-version V] [--keep-temps] [--verbose]
"""

from __future__ import annotations

import argparse
import json
import os
import platform as _platform_mod
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence

_FRAME_RE = re.compile(r"\.(\d{3,})(?=\.[A-Za-z0-9]+$)")

# Cache the platform string at import time so current_platform() never
# calls subprocess internally (platform.system() on Windows uses 'ver').
_SYSTEM = _platform_mod.system()

# The combine step appends 'a.Z' (alpha) from the raw render; map it to A.
DEFAULT_ALPHA_RENAME = {"a.Z": "A"}


def current_platform() -> str:
    """Return the current platform as 'windows', 'linux', or 'darwin'."""
    return {"Windows": "windows", "Linux": "linux",
            "Darwin": "darwin"}.get(_SYSTEM, "linux")


def build_tool_path(root: str, exe_name: str, plat: str) -> str:
    """Build <root>/bin/<exe_name>, appending .exe on Windows if needed."""
    exe = exe_name
    if plat == "windows" and not exe.lower().endswith(".exe"):
        exe = exe + ".exe"
    return f"{root.rstrip('/')}/bin/{exe}"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="luma-denoise OIDN denoise wrapper")
    # Per-OS OIDN roots (new, preferred)
    parser.add_argument("--oidn-root-windows", default="", dest="oidn_root_windows",
                        help="OIDN install root path on Windows workers.")
    parser.add_argument("--oidn-root-linux", default="", dest="oidn_root_linux",
                        help="OIDN install root path on Linux workers.")
    parser.add_argument("--oidn-root-darwin", default="", dest="oidn_root_darwin",
                        help="OIDN install root path on macOS workers.")
    parser.add_argument("--oidn-exe-name", default="oidnDenoise",
                        dest="oidn_exe_name",
                        help="Name of the oidnDenoise executable in <OIDN root>/bin.")
    # Per-OS OIIO roots (new, preferred)
    parser.add_argument("--oiio-root-windows", default="", dest="oiio_root_windows",
                        help="OIIO install root path on Windows workers.")
    parser.add_argument("--oiio-root-linux", default="", dest="oiio_root_linux",
                        help="OIIO install root path on Linux workers.")
    parser.add_argument("--oiio-root-darwin", default="", dest="oiio_root_darwin",
                        help="OIIO install root path on macOS workers.")
    parser.add_argument("--oiio-exe-name", default="oiiotool",
                        dest="oiio_exe_name",
                        help="Name of the oiiotool executable in <OIIO root>/bin.")
    # Legacy resolved-path flags (optional fallback)
    parser.add_argument("--oidn-exe", default="", dest="oidn_exe",
                        help="Absolute path to oidnDenoise (legacy fallback).")
    parser.add_argument("--oiiotool", default="",
                        help="Absolute path to oiiotool (legacy fallback).")
    parser.add_argument("--input", required=True,
                        help="Path to the first frame of the raw sequence.")
    parser.add_argument("--output-dir", required=True, dest="output_dir",
                        help="Directory to write denoised frames into.")
    parser.add_argument("--frame-start", required=True, type=int,
                        dest="frame_start")
    parser.add_argument("--frame-end", required=True, type=int,
                        dest="frame_end")
    parser.add_argument("--beauty-channel", default="beauty",
                        dest="beauty_channel",
                        help="Layer name of the beauty channels in the raw EXR.")
    parser.add_argument("--albedo-channel", default="albedo",
                        dest="albedo_channel",
                        help="Layer name of the albedo guide (REQUIRED in EXR).")
    parser.add_argument("--normal-channel", default="N",
                        dest="normal_channel",
                        help="Layer name of the normal guide (REQUIRED in EXR).")
    parser.add_argument("--addon-version", default="unknown",
                        dest="addon_version")
    parser.add_argument("--keep-temps", action="store_true", dest="keep_temps",
                        help="Keep per-frame temp EXRs for debugging.")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--rename", action="append", default=[],
                        metavar="SRC=DST",
                        help="Beauty channel rename pair recorded in the "
                             "manifest. May be given multiple times. "
                             "Default: derived from the beauty layer channels "
                             "plus a.Z->A.")
    return parser.parse_args(argv)


def _pick_root(args: argparse.Namespace, prefix: str, plat: str) -> str:
    """Return the root for the given tool prefix and platform string."""
    return getattr(args, f"{prefix}_{plat}", "")


def resolve_tools(args: argparse.Namespace, plat: str | None = None):
    """Return (oidn_exe, oiiotool, oidn_root) for the current platform.

    Prefers per-OS install roots; falls back to legacy --oidn-exe/--oiiotool.
    Raises RuntimeError if neither is available for a required tool.
    """
    plat = plat or current_platform()
    oidn_root = _pick_root(args, "oidn_root", plat)
    oiio_root = _pick_root(args, "oiio_root", plat)
    oidn_exe = (build_tool_path(oidn_root, args.oidn_exe_name, plat)
                if oidn_root else args.oidn_exe)
    oiiotool = (build_tool_path(oiio_root, args.oiio_exe_name, plat)
                if oiio_root else args.oiiotool)
    if not oidn_exe:
        raise RuntimeError(
            f"oidn_denoise: no OIDN root for platform '{plat}'. "
            "Set denoise.oidn.oidn_root_path for this OS.")
    if not oiiotool:
        raise RuntimeError(
            f"oidn_denoise: no OIIO root for platform '{plat}'. "
            "Set shared.oiio_root_path for this OS.")
    return oidn_exe, oiiotool, oidn_root


# -- channel reading (mirrors oiio_combine.py; standalone on purpose) -----

_CHANNEL_LIST_RE = re.compile(r"^\s*channel list:\s*(.+)\s*$", re.MULTILINE)
_CHANNEL_TOKEN_RE = re.compile(
    r"([A-Za-z_][A-Za-z0-9_.]*)(?:\s*\([A-Za-z0-9]+\))?")


def _parse_oiiotool_info_channels(stdout: str) -> list:
    match = _CHANNEL_LIST_RE.search(stdout)
    if not match:
        raise RuntimeError(
            "oiiotool --info output did not contain a 'channel list:' line")
    channels = []
    for token in match.group(1).split(","):
        m = _CHANNEL_TOKEN_RE.search(token.strip())
        if m:
            channels.append(m.group(1))
    return channels


def _read_channels_subprocess(path: str, oiiotool_path: str) -> list:
    result = subprocess.run(
        [oiiotool_path, "--info", "-v", path],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"oiiotool --info -v {path} failed with exit {result.returncode}: "
            f"{result.stderr.strip()}"
        )
    return _parse_oiiotool_info_channels(result.stdout)


def _try_import_oiio():
    try:
        import OpenImageIO  # type: ignore
        return OpenImageIO
    except Exception:
        return None


def read_channels(path: str, oiiotool_path: str) -> list:
    """Read channel names with OIIO Python preferred, subprocess fallback."""
    oiio_module = _try_import_oiio()
    if oiio_module is not None:
        inp = oiio_module.ImageInput.open(path)
        if inp is None:
            raise RuntimeError(f"OIIO could not open EXR: {path}")
        try:
            return list(inp.spec().channelnames)
        finally:
            inp.close()
    return _read_channels_subprocess(path, oiiotool_path)


# -- frame and layer helpers ----------------------------------------------

def frame_path(template_path: str, frame: int) -> str:
    """Return template_path with its frame digits replaced by `frame`."""
    dirname = os.path.dirname(template_path)
    basename = os.path.basename(template_path)

    def _sub(m):
        return "." + str(frame).zfill(len(m.group(1)))

    new_name, n = _FRAME_RE.subn(_sub, basename)
    if n == 0:
        raise RuntimeError(
            f"Could not find frame digits in filename: {template_path}")
    return f"{dirname}/{new_name}".replace("\\", "/") if dirname else new_name


def layer_channels(channels: list, layer: str) -> list:
    """Channels belonging to a layer: 'beauty' -> beauty.r/beauty.g/..."""
    grouped = [ch for ch in channels if ch.startswith(layer + ".")]
    if grouped:
        return grouped
    return [ch for ch in channels if ch == layer]


def require_layer(channels: list, layer: str, role: str,
                  settings_field: str) -> list:
    """Return the first 3 channels of a layer or raise an actionable error."""
    found = layer_channels(channels, layer)
    if len(found) < 3:
        raise RuntimeError(
            f"OIDN requires a 3-channel {role} layer but layer '{layer}' "
            f"has {len(found)} channel(s) in the input EXR "
            f"(found: {found or 'none'}). Either the render is missing the "
            f"AOV or the layer name is wrong - configure it via the "
            f"luma-denoise settings field '{settings_field}'."
        )
    return found[:3]


def build_frame_commands(args: argparse.Namespace, in_path: str,
                         out_path: str, channels: list,
                         tmpdir: str):
    """Return (beauty_channels, [argv, ...]) for one frame.

    Command sequence: extract beauty -> extract albedo -> extract normal ->
    oidnDenoise -> rename denoised channels back and write the output frame.
    """
    beauty = require_layer(
        channels, args.beauty_channel, "beauty", "oidn.beauty_channel")
    albedo = require_layer(
        channels, args.albedo_channel, "albedo guide", "oidn.albedo_channel")
    normal = require_layer(
        channels, args.normal_channel, "normal guide", "oidn.normal_channel")

    def t(name):
        return f"{tmpdir}/{name}".replace("\\", "/")

    cmds = [
        [args.oiiotool, in_path, "--ch", ",".join(beauty),
         "-o", t("beauty.exr")],
        [args.oiiotool, in_path, "--ch", ",".join(albedo),
         "-o", t("albedo.exr")],
        [args.oiiotool, in_path, "--ch", ",".join(normal),
         "-o", t("normal.exr")],
        [args.oidn_exe, "--hdr", t("beauty.exr"),
         "--alb", t("albedo.exr"), "--nrm", t("normal.exr"),
         "-o", t("denoised.exr")],
        [args.oiiotool, t("denoised.exr"), "--ch", "R,G,B",
         "--chnames", ",".join(beauty),
         "-o", out_path],
    ]
    return beauty, cmds


def _strip_frame_token(path: str) -> str:
    return _FRAME_RE.sub(lambda m: "." + "#" * len(m.group(1)), path)


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


def build_manifest(args: argparse.Namespace, beauty_channels: list) -> dict:
    basename = os.path.basename(args.input)
    output_pattern = "/".join(
        [args.output_dir.replace("\\", "/").rstrip("/"), basename])
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
    return {
        "denoiser": "oidn",
        "addon_version": args.addon_version,
        "source_pattern": _strip_frame_token(args.input.replace("\\", "/")),
        "output_pattern": _strip_frame_token(output_pattern),
        "beauty_channel_map": beauty_map,
        "guide_channels": {
            "albedo": args.albedo_channel,
            "normal": args.normal_channel,
        },
        "frames": [args.frame_start, args.frame_end],
    }


def write_manifest(output_path: str, manifest: dict) -> bool:
    """Write a sequence-level <name>.denoise.json sidecar (atomic, never raises)."""
    p = Path(output_path)
    name = p.name
    m = _FRAME_RE.search(name)
    base = name[:m.start()] if m else p.stem
    sidecar = p.parent / f"{base}.denoise.json"
    tmp = sidecar.with_name(f".{sidecar.name}.{os.getpid()}.tmp")
    try:
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(manifest, indent=2))
        os.replace(tmp, sidecar)
        return True
    except Exception as e:
        print(f"[oidn_denoise] WARN: could not write manifest {sidecar}: {e}",
              file=sys.stderr)
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


def _run_commands(cmds: list, verbose: bool, env=None) -> int:
    for argv in cmds:
        if verbose:
            print(f"[oidn_denoise] running: {' '.join(argv)}")
        result = subprocess.run(
            argv, capture_output=True, text=True, check=False, env=env)
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        if result.returncode != 0:
            print(f"[oidn_denoise] ERROR: '{argv[0]}' exited with "
                  f"{result.returncode}", file=sys.stderr)
            return result.returncode
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        return _run(args)
    except (RuntimeError, ValueError) as exc:
        print(f"[oidn_denoise] ERROR: {exc}", file=sys.stderr)
        return 1


def _run(args: argparse.Namespace) -> int:
    parse_rename_pairs(args.rename)
    if args.frame_start > args.frame_end:
        raise RuntimeError(
            f"frame_start ({args.frame_start}) > frame_end "
            f"({args.frame_end}) - nothing to denoise.")

    oidn_exe, oiiotool, oidn_root = resolve_tools(args)
    args.oidn_exe = oidn_exe
    args.oiiotool = oiiotool

    env = os.environ.copy()
    if oidn_root:
        env["PATH"] = oidn_root.rstrip("/") + "/bin" + os.pathsep + env.get("PATH", "")

    output_dir = args.output_dir.replace("\\", "/")
    os.makedirs(output_dir, exist_ok=True)

    beauty_channels = []
    for frame in range(args.frame_start, args.frame_end + 1):
        in_path = frame_path(args.input.replace("\\", "/"), frame)
        out_path = f"{output_dir}/{os.path.basename(in_path)}"

        channels = read_channels(in_path, args.oiiotool)
        if args.verbose:
            print(f"[oidn_denoise] frame {frame}: "
                  f"{len(channels)} channels in {in_path}")

        tmpdir = tempfile.mkdtemp(prefix=f"oidn_{frame}_")
        try:
            beauty_channels, cmds = build_frame_commands(
                args, in_path, out_path, channels, tmpdir)
            rc = _run_commands(cmds, args.verbose, env=env)
            if rc != 0:
                return rc
        finally:
            if not args.keep_temps:
                shutil.rmtree(tmpdir, ignore_errors=True)

    manifest = build_manifest(args, beauty_channels)
    sidecar_anchor = f"{output_dir}/{os.path.basename(args.input)}"
    write_manifest(sidecar_anchor, manifest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
