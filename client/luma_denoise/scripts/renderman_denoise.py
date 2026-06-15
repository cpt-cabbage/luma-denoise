"""renderman_denoise.py - Deadline-worker wrapper for Pixar denoise_batch.

Runs once per sequence on a Deadline worker (the denoise job has Frames=1;
denoise_batch processes the whole frame range in one invocation). Invokes
denoise_batch with the flags computed by the submission backend, propagates
its exit code, and on success writes a sequence-level <name>.denoise.json
sidecar next to the denoised frames describing the output channel naming.
The downstream oiio_combine.py wrapper reads that sidecar.

Standalone by design: deploy this single file to a shared filesystem (the
'renderman.wrapper_script_path' luma-denoise setting points at it).

Usage:
    python renderman_denoise.py
        --rmantree-linux /opt/pixar/RenderManProServer-26.3
        --rmantree-windows C:/Pixar/RenderManProServer-26.3
        --rmantree-darwin ""
        --denoise-exe-name denoise_batch
        [--pixar-license 9010@licserver]
        --input <first-frame.exr> --output-dir <dir>
        --frame-start N --frame-end N
        [--cross-frame] [--tiles X Y] [--addon-version V] [--verbose]

    Legacy (single-worker path, kept for backwards compat):
        --denoise-exe <path> --input <first-frame.exr> ...
"""

from __future__ import annotations

import argparse
import json
import os
import platform as _platform_mod
import re
import subprocess
import sys
from pathlib import Path
from typing import Sequence

_FRAME_RE = re.compile(r"\.(\d{3,})(?=\.[A-Za-z0-9]+$)")

# Cache the platform string at import time so current_platform() never
# calls subprocess internally (platform.system() on Windows uses 'ver').
_SYSTEM = _platform_mod.system()

# denoise_batch always emits RenderMan's Ci/a channel convention; the map
# tells the combine step how to rename them for compositing (Nuke R/G/B/A).
# 'a.Z' is appended from the raw render by the combine step, not present in
# the denoised output itself.
RENDERMAN_BEAUTY_MAP = {"Ci.r": "R", "Ci.g": "G", "Ci.b": "B", "a.Z": "A"}


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
        description="luma-denoise RenderMan denoise wrapper")
    # Per-OS RenderMan tree roots (new, preferred)
    parser.add_argument("--rmantree-windows", default="", dest="rmantree_windows",
                        help="RMANTREE root path on Windows workers.")
    parser.add_argument("--rmantree-linux", default="", dest="rmantree_linux",
                        help="RMANTREE root path on Linux workers.")
    parser.add_argument("--rmantree-darwin", default="", dest="rmantree_darwin",
                        help="RMANTREE root path on macOS workers.")
    parser.add_argument("--denoise-exe-name", default="denoise_batch",
                        dest="denoise_exe_name",
                        help="Name of the denoise_batch executable in <RMANTREE>/bin.")
    parser.add_argument("--pixar-license", default="", dest="pixar_license",
                        help="PIXAR_LICENSE_FILE value (e.g. 9010@licserver).")
    # Legacy resolved-path flag (optional fallback)
    parser.add_argument("--denoise-exe", default="", dest="denoise_exe",
                        help="Absolute path to denoise_batch (legacy fallback).")
    parser.add_argument("--input", required=True,
                        help="Path to the first frame of the raw sequence.")
    parser.add_argument("--output-dir", required=True, dest="output_dir",
                        help="Directory to write denoised frames into.")
    parser.add_argument("--frame-start", required=True, type=int,
                        dest="frame_start")
    parser.add_argument("--frame-end", required=True, type=int,
                        dest="frame_end")
    parser.add_argument("--cross-frame", action="store_true",
                        dest="cross_frame",
                        help="Enable cross-frame denoising (-cf).")
    parser.add_argument("--tiles", nargs=2, type=int, default=None,
                        metavar=("X", "Y"),
                        help="Enable tiled denoising with X x Y tiles.")
    parser.add_argument("--addon-version", default="unknown",
                        dest="addon_version",
                        help="luma-denoise addon version, recorded in the manifest.")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--rename", action="append", default=[],
                        metavar="SRC=DST",
                        help="Beauty channel rename pair recorded in the "
                             "manifest. May be given multiple times. "
                             "Default: the built-in RenderMan Ci map.")
    return parser.parse_args(argv)


def resolve_denoise_exe(args: argparse.Namespace, plat: str | None = None):
    """Return (exe_path, rmantree_root) for the current platform.

    Prefers per-OS rmantree roots; falls back to --denoise-exe (legacy).
    Raises RuntimeError if neither is available.
    """
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


def build_denoise_argv(args: argparse.Namespace, denoise_exe: str) -> list:
    argv = [denoise_exe, "-a", "0", "-v", "--clean-alpha", "--progress"]
    if args.cross_frame:
        argv.append("-cf")
    if args.tiles:
        argv.extend(["--tiles", str(args.tiles[0]), str(args.tiles[1])])
    argv.extend(["-o", args.output_dir, args.input,
                 f"{args.frame_start}-{args.frame_end}"])
    return argv


def _strip_frame_token(path: str) -> str:
    """Replace '.NNNN.' frame digits in a path with '.####.' tokens."""
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


def build_manifest(args: argparse.Namespace) -> dict:
    basename = os.path.basename(args.input)
    output_pattern = "/".join(
        [args.output_dir.replace("\\", "/").rstrip("/"), basename])
    return {
        "denoiser": "renderman",
        "addon_version": args.addon_version,
        "source_pattern": _strip_frame_token(args.input.replace("\\", "/")),
        "output_pattern": _strip_frame_token(output_pattern),
        "beauty_channel_map": (parse_rename_pairs(args.rename)
                               if args.rename
                               else dict(RENDERMAN_BEAUTY_MAP)),
        "frames": [args.frame_start, args.frame_end],
    }


def write_manifest(output_path: str, manifest: dict) -> bool:
    """Write a sequence-level <name>.denoise.json sidecar.

    Atomic temp-file rename, same pattern as oiio_combine.py's manifest:
    never corrupt, last writer wins, never raises.
    """
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
        print(f"[renderman_denoise] WARN: could not write manifest "
              f"{sidecar}: {e}", file=sys.stderr)
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        parse_rename_pairs(args.rename)
    except ValueError as exc:
        print(f"[renderman_denoise] ERROR: {exc}", file=sys.stderr)
        return 1

    try:
        denoise_exe, root = resolve_denoise_exe(args)
    except RuntimeError as exc:
        print(f"[renderman_denoise] ERROR: {exc}", file=sys.stderr)
        return 1

    # Build subprocess environment with RMANTREE and license if available.
    env = os.environ.copy()
    if root:
        env["RMANTREE"] = root
        env["PATH"] = f"{root}/bin" + os.pathsep + env.get("PATH", "")
    if args.pixar_license:
        env["PIXAR_LICENSE_FILE"] = args.pixar_license

    denoise_argv = build_denoise_argv(args, denoise_exe)

    if args.verbose:
        print(f"[renderman_denoise] running: {' '.join(denoise_argv)}")

    result = subprocess.run(
        denoise_argv, capture_output=True, text=True, check=False, env=env)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)

    if result.returncode != 0:
        print(f"[renderman_denoise] ERROR: denoise_batch exited with "
              f"{result.returncode}", file=sys.stderr)
        return result.returncode

    manifest = build_manifest(args)
    sidecar_anchor = os.path.join(
        args.output_dir, os.path.basename(args.input))
    write_manifest(sidecar_anchor, manifest)
    return 0


if __name__ == "__main__":
    sys.exit(main())