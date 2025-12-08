import hou
import pyblish.api

from ayon_core.pipeline import AYONPyblishPluginMixin
from ayon_houdini.api import plugin


class ValidateLumaHda(plugin.HoudiniInstancePlugin, AYONPyblishPluginMixin):
    """Validate and configure Luma settings on Karma Render HDA.

    This plugin:
    1. Reads the "denoise" publish attribute and sets rmdenoise_aovs parameter
    2. Detects the renderer engine (CPU/XPU) from the HDA and stores it for
       the Deadline submission plugin to use

    """

    label = "Validate Luma HDA"
    order = pyblish.api.ValidatorOrder
    families = ["usdrender"]

    def find_node_by_name(self, parent_node, name, case_sensitive=False):
        if case_sensitive:
            return parent_node.node(name)

        # Case-insensitive search
        name_lower = name.lower()
        for child in parent_node.allSubChildren():
            if child.name().lower() == name_lower:
                return child

        return None

    def get_denoise_enabled(self, instance):
        """Get the denoise setting from publish attributes."""
        # The BoolDef "denoise" is defined in HoudiniSubmitDeadlineUsdRender plugin
        publish_attributes = instance.data.get("publish_attributes", {})
        deadline_attrs = publish_attributes.get("HoudiniSubmitDeadlineUsdRender", {})
        denoise = deadline_attrs.get("denoise", True)
        self.log.debug(f"Denoise setting from publish attributes: {denoise}")
        return denoise

    def get_engine_from_value(self, engine_value):
        """Normalize engine value to cpu/xpu.

        The engine parameter can contain values like:
        - "cpu", "CPU", "Cpu" -> "cpu"
        - "xpu", "XPU", "Xpu" -> "xpu"

        Args:
            engine_value: The value from the engine parameter on the ROP

        Returns:
            str: "cpu" or "xpu"
        """
        if not engine_value:
            return "cpu"

        engine_lower = str(engine_value).lower().strip()

        if engine_lower == "xpu":
            return "xpu"

        # Default to CPU
        return "cpu"

    def process(self, instance):
        context = instance.context

        # Ensure all previous results are successful
        assert all(
            result["success"] for result in context.data["results"]
        ), "Errors found, aborting integration.."

        variantname = instance.data["variant"]
        self.log.info(f"Variant name: {variantname}")

        # Get the /stage network first
        stage = hou.node('/stage')

        # Find the node within it
        node = self.find_node_by_name(stage, variantname)
        if not node:
            self.log.warning(f"Could not find node '{variantname}' in /stage")
            return

        self.log.info(f"Found node: {node.path()}")

        # --- Denoise Configuration ---
        denoise = self.get_denoise_enabled(instance)
        denoise_parm = node.parm("rmdenoise_aovs")
        if denoise_parm:
            denoise_parm.set(bool(denoise))
        else:
            self.log.debug(f"No rmdenoise_aovs parameter found on {node.path()}")

        # --- Engine Detection ---
        engine_parm = node.parm("engine")
        if engine_parm:
            engine_value = engine_parm.eval()
            detected_engine = self.get_engine_from_value(engine_value)
            instance.data["detected_husk_engine"] = detected_engine
        else:
            self.log.debug(f"No engine parameter found on {node.path()}")

        # --- Procedurals Detection ---
        procedurals_parm = node.parm("enableprocedurals")
        if procedurals_parm:
            enable_procedurals = procedurals_parm.eval()
            instance.data["enable_procedurals"] = bool(enable_procedurals)
        else:
            self.log.debug(f"No enableprocedurals parameter found on {node.path()}")

        # --- Autocrop Detection ---
        autocrop_parm = node.parm("autocrop")
        if autocrop_parm:
            autocrop = autocrop_parm.eval()
            instance.data["autocrop"] = bool(autocrop)
        else:
            self.log.debug(f"No autocrop parameter found on {node.path()}")

        # --- Legacy EXR Detection ---
        legacyexr_parm = node.parm("legacyexr")
        if legacyexr_parm:
            legacyexr = legacyexr_parm.eval()
            instance.data["legacyexr"] = bool(legacyexr)
        else:
            self.log.debug(f"No legacyexr parameter found on {node.path()}")
