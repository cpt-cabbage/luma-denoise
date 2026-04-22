"""oiio_combine.py - Deadline-worker wrapper for the luma-denoise OIIO combine step.

Runs per-frame on a Deadline worker. Reads the denoised and raw EXR channel
lists, computes the set-difference minus exclude patterns, resolves a beauty
rename map, builds an oiiotool command on the fly, and invokes it.

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
import re
import subprocess
import sys
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
                        help="Emit <output>.combine.json per frame.")
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


def _parse_oiiotool_info_channels(stdout: str) -> list[str]:
    """Extract the channel name list from `oiiotool --info -v` stdout.

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


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    # Subsequent tasks will fill in the orchestration body.
    raise NotImplementedError("main not implemented yet")


if __name__ == "__main__":
    sys.exit(main())
