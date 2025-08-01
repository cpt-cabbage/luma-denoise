import os
import subprocess
import pyblish.api

from ayon_core.pipeline import AYONPyblishPluginMixin
from ayon_deadline import abstract_submit_deadline

class LumaDenoiseUsdRender(
        abstract_submit_deadline.AbstractSubmitDeadline,
        AYONPyblishPluginMixin
):
    """Run command-line post-processing on USD files after render is complete."""

    label = "Submit Denoise Pass to Deadline"
    order = pyblish.api.IntegratorOrder + 0.1
    hosts = ["houdini"]
    families = ["usdrender"]
    targets = ["local"]

    def process(self, instance):
        """Plugin entry point."""
        self._instance = instance
        context = instance.context
        self._deadline_url = instance.data["deadline"]["url"]

        assert self._deadline_url, "Requires Deadline Webservice URL"

        job_info = self.get_generic_job_info(instance)
        self.job_info = self.get_job_info(job_info=deepcopy(job_info))

        self._set_scene_path(
            context.data["currentFile"],
            job_info.use_published,
            instance.data.get("stagingDir_is_custom", False)
        )
        if instance.data.get("expectedFiles"):
            self._append_job_output_paths(
                instance,
                self.job_info
            )
        self.plugin_info = self.get_plugin_info()

        self.aux_files = self.get_aux_files()

        job_id = self.process_submission()
        self.log.info(f"Submitted job to Deadline: {job_id}.")

        
        # self.log.info(f"LUMA : Running denoise processing on: {instance.name}")

        # output_dir = instance.data.get("stagingDir")
        # exr_files = instance.data.get("files")  # list of rendered files

        # if not exr_files:
        #     self.log.warning("LUMA : No files found in instance.")
        #     return

        # for exr_file in exr_files:
        #     exr_path = os.path.join(output_dir, exr_file)
        #     cmd = ["echo", "LUMA", exr_path]

        #     self.log.info(f"LUMA : Denoising file: {exr_path}")
        #     try:
        #         subprocess.run(cmd, check=True)
        #     except subprocess.CalledProcessError as e:
        #         raise RuntimeError(f"LUMA : Post-processing failed for {exr_path}: {e}")
