import os
# import subprocess
from dataclasses import dataclass, field, asdict
import pyblish.api

from ayon_core.pipeline import AYONPyblishPluginMixin
from ayon_deadline import abstract_submit_deadline

@dataclass
class CommandLinePluginInfo:
    Executable: str = field(default=None)
    Arguments: str = field(default=None)
    StartupDirectory: str = field(default=None)
    SingleFramesOnly: bool = field(default=False)



class LumaDenoiseUsdRender(
        abstract_submit_deadline.AbstractSubmitDeadline,
        AYONPyblishPluginMixin):
    """Run command-line post-processing on USD rendered EXR files after render is complete.

    This runs the exr's through Pixar's command line denoiser, generating new exr layers
    that then get recombined into the final exr's for publish.
    """

    label = "Submit Denoise Pass to Deadline"
    order = pyblish.api.IntegratorOrder + 0.1
    hosts = ["houdini"]
    families = ["usdrender"]
    targets = ["local"]

    def get_job_info(self, dependency_job_ids=None, job_info=None):
        instance = self._instance
        assert all(
            result["success"] for result in instance.context.data["results"]
        ), "Errors found, aborting integration.."

        project_name = instance.context.data["projectName"]
        filepath = instance.context.data["currentFile"]
        scenename = os.path.basename(filepath)
        job_name = "{scene} - {instance} [DENOISE]".format(scene=scenename, instance=instance.name)
        batch_name = f"{scenename}"

        job_info.Name = job_name
        job_info.BatchName = batch_name
        job_info.Plugin = instance.data.get("plugin", "CommandLine")

        self.log.info(f"llien - get job info ran. DEP JOB INFO = ", type(dependency_job_ids))
        
        return job_info

    def get_plugin_info(self, job_type=None):
        instance = self._instance
        plugin_info = CommandLinePluginInfo(
            Executable="echo",
            Arguments="BOOBS"
        )
        plugin_payload = asdict(plugin_info)
        self.log.info(f"llien - get plugin info ran. JOB TYPE = ", job_type)
        return plugin_payload

    def process(self, instance):

        # self.log.info(instance.context.data)

        # super(LumaDenoiseUsdRender, self).process(instance)
        
        output_dir = os.path.dirname(instance.data["files"][0])
        instance.data["outputDir"] = output_dir
        instance.data["toBeRenderedOn"] = "deadline"
        self.log.info(output_dir)
        self.log.info(f"llien - process ran.")






    
        # """Plugin entry point."""
        # self._instance = instance
        # context = instance.context
        # self._deadline_url = instance.data["deadline"]["url"]

        # assert self._deadline_url, "Requires Deadline Webservice URL"

        # job_info = self.get_generic_job_info(instance)
        # self.job_info = self.get_job_info(job_info=deepcopy(job_info))

        # self._set_scene_path(
        #     context.data["currentFile"],
        #     job_info.use_published,
        #     instance.data.get("stagingDir_is_custom", False)
        # )
        # if instance.data.get("expectedFiles"):
        #     self._append_job_output_paths(
        #         instance,
        #         self.job_info
        #     )
        # self.plugin_info = self.get_plugin_info()

        # self.aux_files = self.get_aux_files()

        # job_id = self.process_submission()
        # self.log.info(f"Submitted job to Deadline: {job_id}.")
        
        
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
