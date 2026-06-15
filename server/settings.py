from typing import Literal, TYPE_CHECKING

from pydantic import validator

from ayon_server.lib.postgres import Postgres
from ayon_server.settings import (
    BaseSettingsModel,
    SettingsField,
    ensure_unique_names,
    normalize_name,
)
from ayon_server.settings.enum import (
    folder_types_enum,
    anatomy_presets_enum,
    addon_all_app_host_names_enum,
)
from ayon_server.types import (
    ColorRGB_hex,
    ColorRGBA_hex,
    ColorRGB_float,
    ColorRGBA_float,
    ColorRGB_uint8,
    ColorRGBA_uint8,
)

if TYPE_CHECKING:
    from ayon_server.addons import BaseServerAddon


class ChannelRenamePair(BaseSettingsModel):
    """A source->target channel rename applied by oiiotool's --chnames."""
    _layout = "compact"
    source: str = SettingsField(
        "",
        title="Source channel",
        description="Exact channel name as it appears in the input EXR (e.g. 'Ci.r').",
    )
    target: str = SettingsField(
        "",
        title="Target channel",
        description="Name to rename it to in the output EXR (e.g. 'R').",
    )


def _denoiser_enum():
    return [
        {"value": "renderman", "label": "Pixar RenderMan (denoise_batch)"},
        {"value": "oidn", "label": "Intel Open Image Denoise (OIDN)"},
    ]


def _output_data_type_enum():
    return ["preserve", "float", "half"]


class MultiplatformPathModel(BaseSettingsModel):
    """One value per worker platform, resolved on the worker at runtime."""
    windows: str = SettingsField("", title="Windows")
    linux: str = SettingsField("", title="Linux")
    darwin: str = SettingsField("", title="macOS")


class RendermanDenoiserSettings(BaseSettingsModel):
    """Pixar RenderMan denoise_batch backend."""

    rmantree_path: MultiplatformPathModel = SettingsField(
        default_factory=lambda: MultiplatformPathModel(
            windows="C:/Program Files/Pixar/RenderManProServer-26.3",
            linux="/opt/pixar/RenderManProServer-26.3",
            darwin="/Applications/Pixar/RenderManProServer-26.3",
        ),
        title="RenderMan Root Path",
        description=(
            "Path to RMANTREE per worker OS. Resolved on the worker at "
            "runtime (these installs aren't in Deadline Path Mapping)."
        ),
    )

    denoise_exe: str = SettingsField(
        "denoise_batch",
        title="Denoiser Executable Name",
        description=(
            "Name of the RenderMan denoiser executable in <RMANTREE>/bin "
            "(the wrapper appends .exe on Windows)."
        ),
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
            "Absolute path to renderman_denoise.py on the shared library. "
            "Single value - Deadline Path Mapping translates it per worker "
            "OS. Supports the {version} token. MUST be set."
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

    oidn_root_path: MultiplatformPathModel = SettingsField(
        default_factory=lambda: MultiplatformPathModel(linux="/opt/oidn"),
        title="OIDN Root Path",
        description=(
            "Path to the OIDN install root per worker OS. Resolved on the "
            "worker at runtime (these installs aren't in Deadline Path "
            "Mapping)."
        ),
    )

    denoise_exe: str = SettingsField(
        "oidnDenoise",
        title="Denoiser Executable Name",
        description=(
            "Name of the OIDN executable in <root>/bin (the wrapper "
            "appends .exe on Windows)."
        ),
    )

    wrapper_script_path: str = SettingsField(
        "",
        title="OIDN Wrapper Script Path (oidn_denoise.py)",
        description=(
            "Absolute path to oidn_denoise.py on the shared library. "
            "Single value - Deadline Path Mapping translates it per worker "
            "OS. Supports the {version} token. MUST be set."
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
            "Absolute path to oiio_combine.py on the shared library. "
            "Single value - Deadline Path Mapping translates it per worker "
            "OS. Supports the {version} token. MUST be set."
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
            "Python that Deadline launches for ALL wrapper scripts (denoise "
            "and combine). Single value - must resolve on every worker's "
            "PATH, or be a Path-Mapped absolute path."
        ),
    )

    oiio_root_path: MultiplatformPathModel = SettingsField(
        default_factory=lambda: MultiplatformPathModel(linux="/opt/oiio"),
        title="OIIO Root Path",
        description=(
            "Path to the OpenImageIO install root per worker OS. Resolved on "
            "the worker at runtime (used by the combine step and by OIDN "
            "channel extraction; not in Deadline Path Mapping)."
        ),
    )

    oiio_exe: str = SettingsField(
        "oiiotool",
        title="oiiotool Executable Name",
        description=(
            "Name of the oiiotool executable in <OIIO root>/bin (the "
            "wrapper appends .exe on Windows)."
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
