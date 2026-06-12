"""oiio_combine.py - Deadline-worker wrapper for the luma-denoise OIIO combine step.

Runs per-frame on a Deadline worker. Reads the denoised and raw EXR channel
lists, computes the set-difference minus exclude patterns, resolves a beauty
rename map, builds an oiiotool command on the fly, and invokes it.

When --write-manifest is set, a single sequence-level <name>.combine.json
sidecar is written next to the combined EXRs (frame token stripped from the
sidecar name so all frames in a sequence write to the same file).

Usage:
    python oiio_combine.py
        --denoised <path> --raw <path> --output <path> --oiiotool <path>
        [--exclude PATTERN ...] [--rename SRC=DST ...]
        [--extra-args "..."] [--compression "zips"]
        [--data-type preserve|float|half]
        [--write-manifest] [--verbose]
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse CLI args. Accepts argv for testing; defaults to sys.argv[1:]."""
    parser = argparse.ArgumentParser(
        description="luma-denoise OIIO combine wrapper",
    )
    parser.add_argument("--denoised", required=True,
                        help="Path to the denoised EXR for this frame.")
    parser.add_argument("--raw", required=True,
                        help="Path to the raw (pre-denoise) EXR for this frame.")
    parser.add_argument("--output", required=True,
                        help="Path to write the combined EXR to.")
    parser.add_argument("--oiiotool", required=True,
                        help="Absolute path to the oiiotool executable.")
    parser.add_argument("--exclude", action="append", default=[],
                        metavar="PATTERN",
                        help="fnmatch glob pattern. Raw channels matching are "
                             "excluded. May be given multiple times.")
    parser.add_argument("--num-default-excludes", type=int, default=0,
                        metavar="N",
                        help="How many of the leading --exclude values came "
                             "from built-in defaults (vs user settings). "
                             "Used only for manifest labeling.")
    parser.add_argument("--rename", action="append", default=[],
                        metavar="SRC=DST",
                        help="Beauty channel rename pair 'source=target'. "
                             "May be given multiple times.")
    parser.add_argument("--extra-args", default="",
                        help="Verbatim string inserted into the oiiotool command.")
    parser.add_argument("--compression", default="",
                        help="Passed to oiiotool as --compression <val>.")
    parser.add_argument("--data-type", default="preserve",
                        choices=["preserve", "float", "half"],
                        help="Output data type policy.")
    parser.add_argument("--write-manifest", action="store_true",
                        help="Emit one <output>.combine.json per sequence "
                             "(frame token stripped from sidecar name).")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Verbose logging.")
    return parser.parse_args(argv)


def _read_channels_oiio(path: str, oiio_module) -> list[str]:
    """Read channel names from an EXR using the OpenImageIO Python module.

    Args:
        path: Absolute path to the EXR to introspect.
        oiio_module: The imported OpenImageIO module (injected for testability).

    Returns:
        List of channel names in file order.

    Raises:
        RuntimeError: If the file cannot be opened.
    """
    inp = oiio_module.ImageInput.open(path)
    if inp is None:
        raise RuntimeError(f"OIIO could not open EXR: {path}")
    try:
        spec = inp.spec()
        return list(spec.channelnames)
    finally:
        inp.close()


_CHANNEL_LIST_RE = re.compile(r"^\s*channel list:\s*(.+)\s*$", re.MULTILINE)
_CHANNEL_TOKEN_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_.]*)(?:\s*\([A-Za-z0-9]+\))?")
_FRAME_RE = re.compile(r"\.(\d{3,})(?=\.[A-Za-z0-9]+$)")


def _parse_oiiotool_info_channels(stdout: str) -> list[str]:
    """Extract the channel name list from `oiiotool --info -v` stdout.

    Returns only the FIRST subimage's channel list (via re.search). Production
    EXRs from RenderMan denoise_batch are always single-subimage, so this is
    not a bug; documented here to prevent a future reader from accidentally
    "fixing" it to concatenate all subimages.

    Args:
        stdout: The stdout captured from invoking `oiiotool --info -v <file>`.

    Returns:
        List of channel names in file order.

    Raises:
        RuntimeError: If no 'channel list:' line is found.
    """
    match = _CHANNEL_LIST_RE.search(stdout)
    if not match:
        raise RuntimeError("oiiotool --info output did not contain a 'channel list:' line")
    body = match.group(1)
    channels: list[str] = []
    for token in body.split(","):
        m = _CHANNEL_TOKEN_RE.search(token.strip())
        if m:
            channels.append(m.group(1))
    return channels


def _read_channels_subprocess(path: str, oiiotool_path: str) -> list[str]:
    """Read channel names by invoking oiiotool --info -v as a subprocess."""
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
    """Attempt to import OpenImageIO. Return the module or None on failure."""
    try:
        import OpenImageIO  # type: ignore
        return OpenImageIO
    except Exception:
        return None


def read_channels(path: str, oiiotool_path: str) -> list[str]:
    """Read channel names with OIIO Python preferred, subprocess fallback."""
    oiio_module = _try_import_oiio()
    if oiio_module is not None:
        return _read_channels_oiio(path, oiio_module)
    return _read_channels_subprocess(path, oiiotool_path)


def apply_exclude_patterns(channels: list[str], patterns: list[str]) -> tuple[list[str], list[str]]:
    """Split channels into (kept, excluded) based on fnmatch glob patterns.

    A pattern matches a channel if:
      - fnmatch.fnmatchcase matches the full channel name, OR
      - fnmatch.fnmatchcase matches the channel's layer (portion before the
        first '.'). This lets "mse" match "mse.r"/"mse.g"/"mse.b" as a group
        without requiring users to write "mse.*".

    Returns:
        (kept, excluded) pair of lists, each preserving input order.
    """
    if not patterns:
        return list(channels), []

    kept: list[str] = []
    excluded: list[str] = []
    for ch in channels:
        layer = ch.split(".", 1)[0]
        matched = any(
            fnmatch.fnmatchcase(ch, p) or fnmatch.fnmatchcase(layer, p)
            for p in patterns
        )
        (excluded if matched else kept).append(ch)
    return kept, excluded


def compute_extra_channels(
    denoised: list[str],
    raw: list[str],
    exclude_patterns: list[str],
) -> list[str]:
    """Return `raw − denoised − excluded`, preserving raw order."""
    denoised_set = set(denoised)
    kept, _ = apply_exclude_patterns(raw, exclude_patterns)
    return [ch for ch in kept if ch not in denoised_set]


def parse_rename_pairs(pairs: list[str]) -> dict[str, str]:
    """Parse ['src=dst', 'src2=dst2'] into a dict.

    Raises:
        ValueError: if any pair is malformed.
    """
    out: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise ValueError(f"Malformed rename pair (expected 'src=dst'): {pair}")
        src, dst = pair.split("=", 1)
        src, dst = src.strip(), dst.strip()
        if not src or not dst:
            raise ValueError(f"Empty source or target in rename pair: {pair}")
        out[src] = dst
    return out


def resolve_chnames(
    final_channels: list[str],
    rename_map: dict[str, str],
) -> list[str] | None:
    """Apply rename_map to final_channels.

    Returns:
        The renamed list if AT LEAST ONE channel was renamed.
        None if no channels match any rename source (caller should skip --chnames).
    """
    matched = False
    out = []
    for ch in final_channels:
        if ch in rename_map:
            out.append(rename_map[ch])
            matched = True
        else:
            out.append(ch)
    return out if matched else None


def build_oiiotool_argv(
    oiiotool: str,
    denoised: str,
    raw: str,
    output: str,
    extra_channels: list[str],
    chnames_override: list[str] | None,
    compression: str,
    data_type: str,
    extra_args: str,
    pass_through: bool,
) -> list[str]:
    """Build the oiiotool command-line argv.

    Structure:
        oiiotool  <denoised>
                  [<raw> --ch <extras> --chappend]   (skipped if pass_through or empty)
                  [--chnames <renamed>]              (skipped if override is None)
                  [--compression <val>]              (skipped if empty)
                  [--format <type>]                  (skipped if preserve)
                  [<extra_args split>]
                  -o <output>
    """
    argv: list[str] = [oiiotool, denoised]

    if not pass_through and extra_channels:
        argv.append(raw)
        argv.append("--ch")
        argv.append(",".join(extra_channels))
        argv.append("--chappend")

    if chnames_override is not None:
        argv.append("--chnames")
        argv.append(",".join(chnames_override))

    if compression:
        argv.append("--compression")
        argv.append(compression)

    if data_type != "preserve":
        argv.append("--format")
        argv.append(data_type)

    if extra_args:
        # posix=False on Windows so backslashes in paths aren't treated as escapes
        # (e.g. --colorconfig C:\tools\ocio\config.ocio).
        argv.extend(shlex.split(extra_args, posix=(sys.platform != "win32")))

    argv.extend(["-o", output])
    return argv


def _strip_frame_token(path: str) -> str:
    """Replace '.NNNN.' frame digits in a path with '.####.' tokens.

    Used to normalize frame-specific paths so the sequence-level manifest
    describes the sequence as a whole rather than the specific frame this
    Deadline worker happened to run.
    """
    return _FRAME_RE.sub(lambda m: "." + "#" * len(m.group(1)), path)


def _sequence_sidecar_path(output_path: str) -> Path:
    """Derive the sequence-level manifest path from a per-frame output path.

    '<dir>/<name>.<NNNN>.<ext>' -> '<dir>/<name>.combine.json'
    '<dir>/<name>.<ext>'        -> '<dir>/<name>.combine.json'
    """
    p = Path(output_path)
    name = p.name
    m = _FRAME_RE.search(name)
    base = name[:m.start()] if m else p.stem
    return p.parent / f"{base}.combine.json"


def _denoise_sidecar_path(denoised_path: str) -> Path:
    """Derive the denoise-manifest path from a per-frame denoised path.

    '<dir>/<name>.<NNNN>.<ext>' -> '<dir>/<name>.denoise.json'
    Written by the denoise wrappers (renderman_denoise.py / oidn_denoise.py).
    """
    p = Path(denoised_path)
    name = p.name
    m = _FRAME_RE.search(name)
    base = name[:m.start()] if m else p.stem
    return p.parent / f"{base}.denoise.json"


def load_denoise_manifest(denoised_path: str) -> dict | None:
    """Load the denoise sidecar next to the denoised frames, if present.

    Returns None when the sidecar is missing or unreadable (warned to
    stderr) - the caller falls back to the CLI rename pairs.
    """
    sidecar = _denoise_sidecar_path(denoised_path)
    if not sidecar.is_file():
        return None
    try:
        return json.loads(sidecar.read_text())
    except Exception as e:
        print(f"[oiio_combine] WARN: unreadable denoise manifest "
              f"{sidecar}: {e}", file=sys.stderr)
        return None


def resolve_rename_map(denoised_path: str, cli_rename_pairs: list[str],
                       pass_through: bool, verbose: bool) -> dict[str, str]:
    """Resolve the beauty rename map: denoise manifest wins over CLI pairs.

    The manifest is authoritative because the denoise wrapper knows exactly
    which channel names it wrote; the CLI pairs come from static settings
    and remain as the fallback (old renders, pass-through mode).
    """
    cli_map = parse_rename_pairs(cli_rename_pairs)
    if pass_through:
        return cli_map
    manifest = load_denoise_manifest(denoised_path)
    if manifest:
        manifest_map = manifest.get("beauty_channel_map") or {}
        if manifest_map:
            if verbose:
                print(f"[oiio_combine] rename map from denoise manifest "
                      f"({manifest.get('denoiser', '?')}): {manifest_map}")
            return {str(k): str(v) for k, v in manifest_map.items()}
    return cli_map


def build_manifest(
    denoised_path: str,
    raw_path: str,
    output_path: str,
    pass_through: bool,
    denoised_channels: list[str],
    raw_channels: list[str],
    exclude_patterns_user: list[str],
    exclude_patterns_default: list[str],
    excluded_channels: list[str],
    appended_channels: list[str],
    chnames_applied: dict[str, str],
    oiiotool_argv: list[str],
) -> dict:
    """Assemble the sequence-level combine manifest dict.

    Frame digits in path-like fields are normalized to '####' so the manifest
    describes the sequence as a whole. Per-frame state (frame number, exit
    code) is intentionally not included — those belong in the Deadline log.
    """
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "denoised_path": _strip_frame_token(denoised_path),
        "raw_path": _strip_frame_token(raw_path),
        "output_path": _strip_frame_token(output_path),
        "pass_through": pass_through,
        "denoised_channels": denoised_channels,
        "raw_channels": raw_channels,
        "exclude_patterns_user": exclude_patterns_user,
        "exclude_patterns_default": exclude_patterns_default,
        "excluded_channels": excluded_channels,
        "appended_channels": appended_channels,
        "chnames_applied": chnames_applied,
        "oiiotool_command": " ".join(_strip_frame_token(a) for a in oiiotool_argv),
    }


def write_manifest(output_path: str, manifest: dict) -> bool:
    """Write a sequence-level <name>.combine.json sidecar.

    All frames in a sequence resolve to the same sidecar path, so concurrent
    Deadline workers race to write it. We write to a PID-unique temp file and
    atomically rename — last writer wins, but the sidecar is never corrupt
    (manifest content is identical across frames, so last-wins is safe).

    Returns:
        True on success, False on any error (logged via stderr, never raised).
    """
    sidecar = _sequence_sidecar_path(output_path)
    tmp = sidecar.with_name(f".{sidecar.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(manifest, indent=2))
        os.replace(tmp, sidecar)
        return True
    except Exception as e:
        print(f"[oiio_combine] WARN: could not write manifest {sidecar}: {e}",
              file=sys.stderr)
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        return _run(args)
    except (RuntimeError, ValueError) as exc:
        # Deadline worker logs this line with a clear prefix; skip the traceback.
        print(f"[oiio_combine] ERROR: {exc}", file=sys.stderr)
        return 1


def _run(args: argparse.Namespace) -> int:
    pass_through = (args.denoised == args.raw)

    if args.verbose:
        print(f"[oiio_combine] denoised={args.denoised}")
        print(f"[oiio_combine] raw={args.raw}")
        print(f"[oiio_combine] output={args.output}")
        print(f"[oiio_combine] pass_through={pass_through}")

    # Read channel lists (runtime introspection).
    denoised_channels = read_channels(args.denoised, args.oiiotool)
    raw_channels = (denoised_channels if pass_through
                    else read_channels(args.raw, args.oiiotool))

    if args.verbose:
        print(f"[oiio_combine] denoised_channels ({len(denoised_channels)}): {denoised_channels}")
        if not pass_through:
            print(f"[oiio_combine] raw_channels ({len(raw_channels)}): {raw_channels}")

    # Compute exclusions + extras.
    # The plugin passes default exclude patterns first, then user patterns.
    # --num-default-excludes N tells us where the split is (manifest only).
    all_excludes = list(args.exclude)
    split_point = min(max(0, args.num_default_excludes), len(all_excludes))
    exclude_patterns_default = all_excludes[:split_point]
    exclude_patterns_user = all_excludes[split_point:]
    extra_channels = ([] if pass_through
                      else compute_extra_channels(denoised_channels, raw_channels, all_excludes))
    _, excluded_channels = apply_exclude_patterns(raw_channels, all_excludes)

    if args.verbose:
        print(f"[oiio_combine] excluded ({len(excluded_channels)}): {excluded_channels}")
        print(f"[oiio_combine] extras   ({len(extra_channels)}): {extra_channels}")

    # Resolve rename map.
    rename_map = resolve_rename_map(
        denoised_path=args.denoised,
        cli_rename_pairs=args.rename,
        pass_through=pass_through,
        verbose=args.verbose,
    )
    final_channels = list(denoised_channels) + list(extra_channels)
    chnames_override = resolve_chnames(final_channels, rename_map)
    if chnames_override is None and rename_map:
        print(f"[oiio_combine] WARN: no channels matched rename map; skipping --chnames",
              file=sys.stderr)

    chnames_applied: dict[str, str] = {}
    if chnames_override is not None:
        for src, dst in rename_map.items():
            if src in final_channels:
                chnames_applied[src] = dst

    # Build argv.
    argv_oiiotool = build_oiiotool_argv(
        oiiotool=args.oiiotool,
        denoised=args.denoised,
        raw=args.raw,
        output=args.output,
        extra_channels=extra_channels,
        chnames_override=chnames_override,
        compression=args.compression,
        data_type=args.data_type,
        extra_args=args.extra_args,
        pass_through=pass_through,
    )

    if args.verbose:
        print(f"[oiio_combine] running: {' '.join(argv_oiiotool)}")

    # Run.
    result = subprocess.run(argv_oiiotool, capture_output=True, text=True, check=False)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)

    # Manifest.
    if args.write_manifest:
        manifest = build_manifest(
            denoised_path=args.denoised,
            raw_path=args.raw,
            output_path=args.output,
            pass_through=pass_through,
            denoised_channels=denoised_channels,
            raw_channels=raw_channels,
            exclude_patterns_user=exclude_patterns_user,
            exclude_patterns_default=exclude_patterns_default,
            excluded_channels=excluded_channels,
            appended_channels=extra_channels,
            chnames_applied=chnames_applied,
            oiiotool_argv=argv_oiiotool,
        )
        write_manifest(args.output, manifest)

    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
