import os
from copy import deepcopy
from dataclasses import dataclass, field, asdict

import pyblish.api

from ayon_core.pipeline import AYONPyblishPluginMixin
from ayon_deadline import abstract_submit_deadline


# Default exclude patterns mirror the server settings default. Kept here as a
# safety net in case the settings entry is missing from project bundles.
DEFAULT_EXCLUDE_PATTERNS: list[str] = ["*_mse", "mse", "sampleCount"]

# Default rename maps. Settings override these per-project.
DEFAULT_RENAME_DENOISED: list[dict[str, str]] = [
    {"source": "Ci.r", "target": "R"},
    {"source": "Ci.g", "target": "G"},
    {"source": "Ci.b", "target": "B"},
    {"source": "a.Z", "target": "A"},
]
DEFAULT_RENAME_RAW: list[dict[str, str]] = [
    {"source": "beauty.r", "target": "R"},
    {"source": "beauty.g", "target": "G"},
    {"source": "beauty.b", "target": "B"},
    {"source": "a.Z", "target": "A"},
]


@dataclass
class CommandLinePluginInfo:
    Executable: str = field(default=None)
    Arguments: str = field(default=None)
    StartupDirectory: str = field(default=None)
    SingleFramesOnly: bool = field(default=False)
    ShellExecute: bool = field(default=False)
    Shell: str = field(default=None)


class ExtractOiioCombine(
        abstract_submit_deadline.AbstractSubmitDeadline,
        AYONPyblishPluginMixin):
    """Submit a Deadline CommandLine job that runs oiio_combine.py per frame.

    The wrapper script reads channel lists of the denoised and raw EXRs at
    runtime, computes the set-difference minus exclude patterns, renames the
    beauty layer to R/G/B/A (Nuke convention), and invokes oiiotool to emit
    the final combined EXR.

    Stubs filled in at submit time:
      - dependency (denoise job, or render job in pass-through)
      - file paths and frame-token substitutions
      - setting-driven wrapper CLI flags

    Channel selection and rename logic lives in the wrapper script, not here.
    """

    label = "Submit OIIO Combine to Deadline"
    order = pyblish.api.IntegratorOrder + 0.11
    hosts = ["houdini"]
    families = ["usdrender"]
    targets = ["local"]

    # Methods are added in Tasks 12 and 13.
    pass
