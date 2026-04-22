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


class LumaDenoiseSettings(BaseSettingsModel):
    """
    """
    denoise_enabled: bool = SettingsField(
        False,
        title="Enable Denoising",
        description="Enable automatic denoising of rendered EXR files",
    )

    denoise_deadline_priority: int = SettingsField(
        50,
        title="Priority",
        description="Deadline job priority",
    )

    denoise_pool: str = SettingsField(
        "luma",
        title="Pool",
        description="Pool to use for denoising",
    )

    denoise_group: str = SettingsField(
        "denoise_group",
        title="Group",
        description="Group to use for denoising",
    )

    denoise_rmantree_path: str = SettingsField(
        "/opt/pixar/RenderManProServer-26.3",
        title="Renderman Root Path",
        description="Path to RMAN ROOT",
    )

    denoise_exe: str = SettingsField(
        "denoise_batch",
        title="Renderman Denoise Name",
        description="Name of the denoiser executable",
    )

    denoise_pixar_lic: str = SettingsField(
        "9010@192.168.35.28",
        title="Renderman License Server",
        description="Renderman license server or file location",
    )

    # OIIO combine settings - always enabled by default
    oiio_enabled: bool = SettingsField(
        True,
        title="Enable OIIO Combine",
        description="Enable OIIO combine processing (always enabled)",
    )

    oiio_root_path: str = SettingsField(
        "/opt/oiio",
        title="OIIO Root Path",
        description="Path to OIIO installation root",
    )

    oiio_exe: str = SettingsField(
        "oiiotool",
        title="OIIO Executable Name",
        description="Name of the OIIO executable",
    )

    combine_deadline_priority: int = SettingsField(
        50,
        title="OIIO Combine Deadline Priority",
        description="Deadline job priority for OIIO combine",
    )

    combine_pool: str = SettingsField(
        "default",
        title="OIIO Combine Pool",
        description="Deadline pool for OIIO combine jobs",
    )

    combine_group: str = SettingsField(
        "default",
        title="OIIO Combine Group",
        description="Deadline group for OIIO combine jobs",
    )

    # --- Wrapper-script tunables ---
    wrapper_script_path: str = SettingsField(
        "",
        title="Wrapper Script Path",
        description=(
            "Absolute path to oiio_combine.py on a shared filesystem "
            "accessible from both the submitting machine AND every Deadline "
            "render node. Supports the {version} token — substituted at "
            "submission time with the luma-denoise addon version, so the "
            "same template can survive addon upgrades. "
            "Example: 'L:/tools/.../luma_denoise_scripts/{version}/oiio_combine.py'. "
            "MUST be configured for the OIIO combine step to submit — leaving "
            "this empty will raise a clear error during publish."
        ),
    )

    python_executable: str = SettingsField(
        "python",
        title="Python Executable",
        description=(
            "Python used on the Deadline worker to run the combine wrapper "
            "script. Absolute path or a name resolvable on the worker PATH."
        ),
    )

    run_when_denoise_disabled: bool = SettingsField(
        False,
        title="Run OIIO Combine when denoise is disabled",
        description=(
            "Only applies when denoise did NOT run for an instance. "
            "When False (default), the OIIO combine job is skipped entirely "
            "and publish pulls from the raw render directory. When True, "
            "the combine job runs as a pass-through over the raw render "
            "(useful if downstream tooling needs 'combined/' as a consistent "
            "publish location). When denoise did run, the combine job "
            "always runs regardless of this flag."
        ),
    )

    channel_exclude_patterns: list[str] = SettingsField(
        default_factory=lambda: ["*_mse", "mse", "sampleCount"],
        title="Channel Exclude Patterns",
        description=(
            "fnmatch glob patterns. Any raw-render channel whose name matches "
            "any pattern is excluded from the combined output. Defaults strip "
            "denoiser-internal variance/guidance channels that have no "
            "compositing value."
        ),
    )

    beauty_rename_map_denoised: list[ChannelRenamePair] = SettingsField(
        default_factory=lambda: [
            ChannelRenamePair(source="Ci.r", target="R"),
            ChannelRenamePair(source="Ci.g", target="G"),
            ChannelRenamePair(source="Ci.b", target="B"),
            ChannelRenamePair(source="a.Z", target="A"),
        ],
        title="Beauty Rename Map (denoised)",
        description=(
            "Rename applied to the output's primary beauty channels when "
            "denoise ran (RenderMan Ci/a convention → Nuke R/G/B/A)."
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
            "Rename applied when denoise did not run and OIIO is running "
            "in pass-through mode. Uses traditional non-denoised AOV naming."
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
            "Passed to oiiotool as --compression <val>. Empty string disables "
            "the flag. Common values: 'zips' (fast, default), 'zip' (slower, "
            "slightly smaller), 'piz' (smaller for natural images, slower)."
        ),
    )

    output_data_type: str = SettingsField(
        "preserve",
        title="Output Data Type",
        description=(
            "'preserve' keeps oiiotool's default per-channel types (depth "
            "stays float, beauty stays half — required for Nuke). 'float' "
            "and 'half' force uniform output precision."
        ),
        enum_resolver=lambda: ["preserve", "float", "half"],
    )

    write_combine_manifest: bool = SettingsField(
        True,
        title="Write Per-Frame Combine Manifest",
        description=(
            "When True, the wrapper writes a <output>.combine.json sidecar "
            "per frame recording every channel decision. Useful for "
            "debugging; disable once the pipeline is stable."
        ),
    )

    wrapper_verbose_logging: bool = SettingsField(
        True,
        title="Verbose Wrapper Logging",
        description=(
            "Toggles the wrapper script's -v flag. When True, channel lists "
            "and the full oiiotool command are logged to the Deadline task "
            "log."
        ),
    )

    # Output configuration
    output_subdirectory: str = SettingsField(
        "combined",
        title="Output Subdirectory",
        description="Subdirectory for combined output files",
    )

    preserve_intermediates: bool = SettingsField(
        False,
        title="Preserve Intermediates",
        description="Keep intermediate files after processing",
    )

