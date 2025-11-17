import re
import os

import hou
import pyblish.api

from ayon_core.pipeline import AYONPyblishPluginMixin
from ayon_houdini.api import plugin


class ValidateDenoiseHda(plugin.HoudiniInstancePlugin, AYONPyblishPluginMixin):
    """Validate and set denoise parameter on Karma Render HDA

    Reads the "denoise" publish attribute and sets the rmdenoise_aovs parameter
    on the Houdini render node accordingly.

    """

    label = "Validate Denoise HDA"
    order = pyblish.api.ValidatorOrder 
    families = ["usdrender"]
    
    def find_node_by_name(self,parent_node, name, case_sensitive=False):

        if case_sensitive:
            return parent_node.node(name)
        
        # Case-insensitive search
        name_lower = name.lower()
        for child in parent_node.allSubChildren():
            if child.name().lower() == name_lower:
                return child
    
        return None
    
    def get_denoise_enabled(self, instance):
        """Get the denoise setting from publish attributes"""
        # The BoolDef "denoise" is defined in HoudiniSubmitDeadlineUsdRender plugin
        # We need to access it from that plugin's namespace in publish_attributes
        publish_attributes = instance.data.get("publish_attributes", {})
        deadline_attrs = publish_attributes.get("HoudiniSubmitDeadlineUsdRender", {})
        denoise = deadline_attrs.get("denoise", "HIII")  # Default to True as defined in the BoolDef
        self.log.debug(f"Denoise setting from publish attributes: {denoise}")
        return denoise

    def process(self, instance):
        context = instance.context

        # Ensure all previous results are successful
        assert all(
            result["success"] for result in context.data["results"]
        ), "Errors found, aborting integration.."

        # Get denoise setting from publish attributes
        denoise = self.get_denoise_enabled(instance)
        variantname = instance.data["variant"]

        # Get the /stage network first
        stage = hou.node('/stage')

        # Then get the node within it
        node = self.find_node_by_name(stage,variantname)

        # Set the rmdenoise_aovs parameter on the render node
        node.parm("rmdenoise_aovs").set(bool(denoise))  

        self.log.info(f"Setting denoise on {variantname} to {denoise}")
                
                

        