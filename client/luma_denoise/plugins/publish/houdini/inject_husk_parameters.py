"""Build Luma Husk parameters for Deadline render job.

This plugin builds the plugin_info_data dictionary that gets applied to
both export and render jobs via ayon-deadline's abstract_submit_deadline.
"""

import pyblish.api

from ayon_core.pipeline import AYONPyblishPluginMixin


class InjectHuskParameters(pyblish.api.InstancePlugin, AYONPyblishPluginMixin):
    """Build Luma Husk parameters for Deadline jobs.

    This plugin runs before Deadline submission and builds the plugin_info_data
    dictionary from values detected by ValidateLumaHda:
    - Engine: "cpu" or "xpu"
    - AllowedProcedurals: "none" or "basic"
    - Autocrop: AOV list for autocrop
    - ExrMode: "0" for legacy, "1" for default
    """

    label = "Build Husk Parameters"
    order = pyblish.api.IntegratorOrder - 0.02  # Before Deadline submission
    hosts = ["houdini"]
    families = ["usdrender"]
    targets = ["local"]

    # Default values
    default_engine = "cpu"
    enabled = True

    def process(self, instance):
        # Skip if not rendering on farm
        if not instance.data.get("farm"):
            self.log.debug("Not rendering on farm, skipping parameter building.")
            return

        # Build the luma parameters
        luma_params = self._build_luma_params(instance)


        # Initialize deadline data structure if needed
        if "deadline" not in instance.data:
            instance.data["deadline"] = {}
        if not instance.data["deadline"].get("plugin_info_data"):
            instance.data["deadline"]["plugin_info_data"] = {}

        # Add to plugin_info_data
        instance.data["deadline"]["plugin_info_data"].update(luma_params)

    def _build_luma_params(self, instance):
        """Build the dictionary of Luma-specific Husk parameters."""
        params = {}

        # Engine
        engine = instance.data.get("detected_husk_engine", self.default_engine)
        params["Engine"] = engine
        self.log.info(f"Setting Husk engine to: {engine}")

        # Procedurals
        enable_procedurals = instance.data.get("enable_procedurals", True)
        if not enable_procedurals:
            self.log.info("Procedurals disabled")
            params["AllowedProcedurals"] = "none"
        else:
            self.log.info("Procedurals enabled")
            params["AllowedProcedurals"] = "basic"

        # Autocrop
        autocrop = instance.data.get("autocrop", False)
        if autocrop:
            self.log.info("Autocrop enabled")
            params["Autocrop"] = "C,A,a,holdout_shadows,beauty,AO"
        else:
            params["Autocrop"] = "*"

        # Legacy EXR
        legacyexr = instance.data.get("legacyexr", False)
        if legacyexr:
            self.log.info("Legacy EXR enabled")
            params["ExrMode"] = "0"
        else:
            self.log.info("Legacy EXR disabled")
            params["ExrMode"] = "1"

        return params
