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

    denoise_exe_path: str = SettingsField(
        "/opt/pixar/RenderManProServer-26.3/bin/denoise_batch",
        title="Renderman Denoise Exec Path",
        description="Path to the linux denoise executable",
    )

    textarea: str = SettingsField(
        "",
        title="Textarea",
        widget="textarea",
        placeholder="Placeholder of the textarea field",
    )

    number: int = SettingsField(
        1,
        title="Number",
        description="Positive integer 1-10",
        gt=0,  # greater than
        le=10,  # less or equal
        placeholder="Placeholder of the number field",
    )
