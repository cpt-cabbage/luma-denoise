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

class LumaDenoiseSettings(BaseSettingsModel):
    """
    """
    rmantree_path: str = SettingsField(
        "/opt/pixar/RenderManProServer-26.3",
        title="Renderman Root Path",
        description="Path to RMAN ROOT",
    )

    denoise_exe: str = SettingsField(
        "denoise_batch",
        title="Renderman Denoise Exec Name",
        description="Name of the denoiser executable",
    )

    pixar_lic: str = SettingsField(
        "9010@192.168.35.28",
        title="Renderman License Server",
        description="Renderman license server or file location",
    )

