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


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    # Subsequent tasks will fill in the orchestration body.
    raise NotImplementedError("main not implemented yet")


if __name__ == "__main__":
    sys.exit(main())
