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
    """A source→target channel rename applied by oiiotool's --chnames."""
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
            "When False, the OIIO combine job is only submitted if denoise "
            "actually ran. When True, the combine job runs as a pass-through "
            "over the raw render (useful if downstream tooling needs "
            "'combined/' as a consistent publish location)."
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
            "Raw string inserted verbatim into the oiiotool command between "
            "--chnames and -o. Useful for --planarconfig, --tile, --iconfig, "
            "etc."
        ),
    )

    output_compression: str = SettingsField(
        "zips:16",
        title="Output Compression",
        description=(
            "Passed to oiiotool as --compression <val>. Empty string disables "
            "the flag."
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

    # AOV configuration options
    include_aovs: bool = SettingsField(
        True,
        title="Include AOVs",
        description="Include AOVs in combine processing",
    )

    crypto_materials: bool = SettingsField(
        True,
        title="Crypto Materials",
        description="Include crypto materials AOV",
    )

    crypto_primitives: bool = SettingsField(
        False,
        title="Crypto Primitives",
        description="Include crypto primitives AOV",
    )

    diffuse: bool = SettingsField(
        True,
        title="Diffuse",
        description="Include diffuse AOV",
    )

    specular: bool = SettingsField(
        True,
        title="Specular",
        description="Include specular AOV",
    )

    albedo: bool = SettingsField(
        False,
        title="Albedo",
        description="Include albedo AOV",
    )

    normals: bool = SettingsField(
        True,
        title="Normals",
        description="Include normals AOV",
    )

    position: bool = SettingsField(
        True,
        title="Position",
        description="Include position AOV",
    )

    uv: bool = SettingsField(
        False,
        title="UV",
        description="Include UV AOV",
    )

    depth: bool = SettingsField(
        False,
        title="Depth",
        description="Include depth AOV",
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

