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
        --denoise-exe <path> --input <first-frame.exr> --output-dir <dir>
        --frame-start N --frame-end N
        [--cross-frame] [--tiles X Y] [--addon-version V] [--verbose]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Sequence

_FRAME_RE = re.compile(r"\.(\d{3,})(?=\.[A-Za-z0-9]+$)")

# denoise_batch always emits RenderMan's Ci/a channel convention; the map
# tells the combine step how to rename them for compositing (Nuke R/G/B/A).
# 'a.Z' is appended from the raw render by the combine step, not present in
# the denoised output itself.
RENDERMAN_BEAUTY_MAP = {"Ci.r": "R", "Ci.g": "G", "Ci.b": "B", "a.Z": "A"}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="luma-denoise RenderMan denoise wrapper")
    parser.add_argument("--denoise-exe", required=True, dest="denoise_exe",
                        help="Absolute path to denoise_batch.")
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
    return parser.parse_args(argv)


def build_denoise_argv(args: argparse.Namespace) -> list:
    argv = [args.denoise_exe, "-a", "0", "-v", "--clean-alpha", "--progress"]
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


def build_manifest(args: argparse.Namespace) -> dict:
    basename = os.path.basename(args.input)
    output_pattern = "/".join(
        [args.output_dir.replace("\\", "/").rstrip("/"), basename])
    return {
        "denoiser": "renderman",
        "addon_version": args.addon_version,
        "source_pattern": _strip_frame_token(args.input.replace("\\", "/")),
        "output_pattern": _strip_frame_token(output_pattern),
        "beauty_channel_map": dict(RENDERMAN_BEAUTY_MAP),
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
    denoise_argv = build_denoise_argv(args)

    if args.verbose:
        print(f"[renderman_denoise] running: {' '.join(denoise_argv)}")

    result = subprocess.run(
        denoise_argv, capture_output=True, text=True, check=False)
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
