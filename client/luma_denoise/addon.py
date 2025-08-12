import os

from ayon_core.addon import (
    AYONAddon, IPluginPaths
)

from .version import __version__

LUMA_DENOISE_HOST_DIR = os.path.dirname(os.path.abspath(__file__))

class LumaDenoiseAddon(
    AYONAddon,
    IPluginPaths,
):
    name = "luma-denoise"
    version = __version__
    host_name = "houdini"
    enabled = True

    def initialize(self, studio_settings):
        denoise_settings = studio_settings[self.name]
        # self.log.info(f"LUMA : Loaded Denoise Addon.")
    def get_plugin_paths(self):
        return {
            "publish": [os.path.join(LUMA_DENOISE_HOST_DIR, "plugins", "publish")]
        }

    def add_implementation_envs(self, env, _app):
        # Add requirements to LUMA_DENOISE_EXTENSION_PATHS
        pass
