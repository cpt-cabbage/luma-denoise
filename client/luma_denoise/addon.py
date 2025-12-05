import os
from typing import List, Optional

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
        self.log.info("LUMA Denoise Addon initialized.")

    def get_plugin_paths(self):
        # Return empty dict - we use get_publish_plugin_paths for host-specific paths
        return {
            "actions": [os.path.join(LUMA_DENOISE_HOST_DIR, "plugins", "actions")]
        }

    def get_publish_plugin_paths(
        self,
        host_name: Optional[str] = None
    ) -> List[str]:
        """Return publish plugin paths based on host.

        Only returns paths for Houdini to prevent import errors in other hosts.
        """
        publish_dir = os.path.join(LUMA_DENOISE_HOST_DIR, "plugins", "publish")
        paths = []
        # Only add houdini plugins when running in houdini
        if host_name == "houdini":
            paths.append(os.path.join(publish_dir, "houdini"))
        return paths

    def add_implementation_envs(self, env, _app):
        # Add requirements to LUMA_DENOISE_EXTENSION_PATHS
        pass
