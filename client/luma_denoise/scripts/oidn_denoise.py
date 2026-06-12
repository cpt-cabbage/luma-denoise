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
'oidn.wrapper_script_path' luma-denoise setting points at it). The channel
reading block mirrors oiio_combine.py on purpose - wrappers do not import
each other.

Usage:
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
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence

_FRAME_RE = re.compile(r"\.(\d{3,})(?=\.[A-Za-z0-9]+$)")

# The combine step appends 'a.Z' (alpha) from the raw render; map it to A.
DEFAULT_ALPHA_RENAME = {"a.Z": "A"}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="luma-denoise OIDN denoise wrapper")
    parser.add_argument("--oidn-exe", required=True, dest="oidn_exe",
                        help="Absolute path to oidnDenoise.")
    parser.add_argument("--oiiotool", required=True,
                        help="Absolute path to oiiotool (channel extraction).")
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


def _run_commands(cmds: list, verbose: bool) -> int:
    for argv in cmds:
        if verbose:
            print(f"[oidn_denoise] running: {' '.join(argv)}")
        result = subprocess.run(
            argv, capture_output=True, text=True, check=False)
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
            rc = _run_commands(cmds, args.verbose)
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
