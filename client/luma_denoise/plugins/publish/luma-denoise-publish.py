import os
from copy import deepcopy
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
        context = instance.context

        # Ensure all previous results are successful
        assert all(
            result["success"] for result in context.data["results"]
        ), "Errors found, aborting integration.."

        # Get denoise settings from project settings
        project_settings = context.data["project_settings"]
        denoise_settings = project_settings.get("luma-denoise", {})

        filepath = context.data["currentFile"]
        scenename = os.path.basename(filepath)
        job_name = "{scene} - {instance} [DENOISE]".format(scene=scenename, instance=instance.name)
        batch_name = f"{scenename}"

        job_info.Name = job_name
        job_info.BatchName = batch_name
        job_info.Plugin = "CommandLine"

        # Set job priority from settings
        job_priority = denoise_settings.get("denoise_deadline_priority", 50)
        job_info.Priority = job_priority

        # Set pool from settings
        job_pool = denoise_settings.get("denoise_pool", "denoise_pool")
        job_info.Pool = job_pool

        # Set job dependencies if provided (should depend on render job)
        if dependency_job_ids:
            job_info.JobDependencies = dependency_job_ids

        # Set frames to match the render frames since denoising is per-frame
        # Use the same frame range as the render job
        start_frame = instance.data.get("frameStartHandle", 1)
        end_frame = instance.data.get("frameEndHandle", 1)
        step = instance.data.get("byFrameStep", 1)
        job_info.Frames = f"{int(start_frame)}-{int(end_frame)}x{int(step)}"

        # Set output directories and filenames for denoising results
        for i, filepath in enumerate(instance.data["files"]):
            dirname = os.path.dirname(filepath)
            fname = os.path.basename(filepath)
            # For denoising, output goes to same directory but with _denoised suffix
            denoised_fname = fname.replace('.exr', '_denoised.exr')
            job_info.OutputDirectory += dirname.replace("\\", "/")
            job_info.OutputFilename += denoised_fname

        self.log.info(f"Job info configured for denoising: {job_name} (Priority: {job_priority}, Pool: {job_pool})")

        return job_info

    def get_plugin_info(self, job_type=None):
        instance = self._instance
        files = instance.data["files"]

        # Get denoise settings from project settings
        project_settings = instance.context.data["project_settings"]
        denoise_settings = project_settings.get("luma-denoise", {})

        # Build the executable path from settings
        rman_root = denoise_settings.get("denoise_rmantree_path", "/opt/pixar/RenderManProServer-26.3")
        denoise_exe_name = denoise_settings.get("denoise_exe", "denoise_batch")
        executable_path = os.path.join(rman_root, "bin", denoise_exe_name)

        # For denoising, we need to handle per-frame processing
        # Deadline will substitute frame numbers in the arguments
        if files:
            # Use Deadline's frame substitution: <STARTFRAME>, <ENDFRAME>, etc.
            # For EXR files, typically named like: scene_render.1001.exr
            first_file = files[0]
            dirname = os.path.dirname(first_file)
            basename = os.path.basename(first_file)

            # Extract frame pattern - assuming files follow naming convention
            # This is a simplified approach - in practice you might need more robust parsing
            frame_pattern = basename.replace('.exr', '.<STARTFRAME>.exr')
            output_pattern = basename.replace('.exr', '.<STARTFRAME>_denoised.exr')

            # Build the denoise command arguments using Deadline frame substitution
            arguments = f'--input "{os.path.join(dirname, frame_pattern)}" --output "{os.path.join(dirname, output_pattern)}"'

            plugin_info = CommandLinePluginInfo(
                Executable=executable_path,
                Arguments=arguments,
                StartupDirectory=dirname,
                SingleFramesOnly=True  # Each frame is processed independently
            )
        else:
            # Fallback if no files
            plugin_info = CommandLinePluginInfo(
                Executable="echo",
                Arguments="No files to denoise",
                SingleFramesOnly=True
            )

        plugin_payload = asdict(plugin_info)
        self.log.info(f"Plugin info configured for denoising: {len(files)} files using {executable_path}")
        return plugin_payload

    def process(self, instance):
        self._instance = instance

        # Check if denoising is enabled in project settings
        project_settings = instance.context.data["project_settings"]
        denoise_settings = project_settings.get("luma-denoise", {})
        if not denoise_settings.get("denoise_enabled", False):
            self.log.info("Denoising disabled in project settings, skipping.")
            return

        # Ensure we have files to denoise
        if not instance.data.get("files"):
            self.log.warning("No files found for denoising.")
            return

        # For denoising, we need to depend on the render job, not submit immediately
        # The render job will have already been submitted by the main render plugin
        # We need to find the render job ID and submit our denoise job as dependent

        # Get the render job ID from the instance data (set by the render plugin)
        render_job_id = instance.data.get("deadline", {}).get("job_info", {}).get("job_id")
        if not render_job_id:
            # Try alternative locations where the job ID might be stored
            render_job_id = instance.data.get("deadlineSubmissionJob", {}).get("_id")
            if not render_job_id:
                self.log.warning("Could not find render job ID for dependency. Skipping denoise submission.")
                return

        # Set up our denoise job with dependency on the render job
        context = instance.context
        self._deadline_url = instance.data["deadline"]["url"]

        assert self._deadline_url, "Requires Deadline Webservice URL"

        # Get generic job info and customize for denoising
        job_info = self.get_generic_job_info(instance)
        self.job_info = self.get_job_info(job_info=deepcopy(job_info), dependency_job_ids=[render_job_id])

        # Set up plugin info for denoising
        self.plugin_info = self.get_plugin_info()
        self.aux_files = self.get_aux_files()

        # Apply any additional plugin info data
        plugin_info_data = instance.data["deadline"]["plugin_info_data"]
        if plugin_info_data:
            self.apply_additional_plugin_info(plugin_info_data)

        # Submit the denoise job
        job_id = self.process_submission()
        self.log.info(f"Submitted denoise job to Deadline: {job_id} (depends on render job {render_job_id})")

        # Store output directory for unified publisher
        output_dir = os.path.dirname(instance.data["files"][0])
        instance.data["outputDir"] = output_dir
        instance.data["toBeRenderedOn"] = "deadline"

        instance.data["deadline"]["job_info"] = deepcopy(self.job_info)






    
