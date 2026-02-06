"""Force default publish attributes for USD render instances.

Overrides specific publish attribute values after AYON's collectors
have populated them, ensuring artists always submit with studio
defaults regardless of what the publisher UI shows.
"""

import pyblish.api

from ayon_houdini.api import plugin


class CollectForceRenderDefaults(plugin.HoudiniInstancePlugin):
    """Force render publish attributes to studio defaults.

    Runs after AYON's collectors have built publish_attributes,
    overriding values that artists should not change.
    """

    label = "Force Render Defaults"
    order = pyblish.api.CollectorOrder + 0.35
    families = ["usdrender"]

    # --- Studio defaults ---
    # Set to None to leave a value alone, or set the forced value.

    # "farm" = Farm Rendering - Split export & render jobs
    # "local" = Local rendering
    # "local_no_render" = Local - no render (publish existing frames)
    render_target = "farm_split"

    # Review toggle
    review = True

    # Frames Per Task for render jobs
    frames_per_task = None  # None = don't override

    # Priority
    priority = None  # None = don't override

    # Group
    group = None  # None = don't override

    def process(self, instance):
        publish_attrs = instance.data.get("publish_attributes", {})
        deadline_attrs = publish_attrs.get(
            "HoudiniSubmitDeadlineUsdRender", {}
        )

        if not deadline_attrs:
            return

        overrides = {}

        if self.render_target is not None:
            overrides["render_target"] = self.render_target

        if self.review is not None:
            overrides["review"] = self.review

        if self.frames_per_task is not None:
            overrides["export_chunk"] = self.frames_per_task

        if self.priority is not None:
            overrides["export_priority"] = self.priority

        if self.group is not None:
            overrides["export_group"] = self.group

        if overrides:
            deadline_attrs.update(overrides)
            self.log.info(f"Forced render defaults: {overrides}")
