import os
from copy import deepcopy
from dataclasses import dataclass, field, asdict
import pyblish.api

from ayon_core.pipeline import AYONPyblishPluginMixin
from ayon_deadline import abstract_submit_deadline

from luma_denoise.denoisers import get_denoiser_backend

@dataclass
class CommandLinePluginInfo:
    Executable: str = field(default=None)
    Arguments: str = field(default=None)
    StartupDirectory: str = field(default=None)
    SingleFramesOnly: bool = field(default=False)
    ShellExecute: bool = field(default=False)
    Shell: str = field(default=None)

class LumaDenoiseUsdRender(
        abstract_submit_deadline.AbstractSubmitDeadline,
        AYONPyblishPluginMixin):
    """Submit the denoise pass for USD-rendered EXR sequences to Deadline.

    The denoiser backend (RenderMan denoise_batch or Intel OIDN) is selected
    by the 'denoiser' luma-denoise project setting. Each backend runs as a
    Python wrapper script on the worker and writes a <seq>.denoise.json
    sidecar that the downstream OIIO combine job reads.
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
        denoise_settings = project_settings["luma-denoise"].get("denoise", {})

        filepath = context.data["currentFile"]
        scenename = os.path.basename(filepath)
        backend_tag = self._backend.name.upper()
        job_name = "{scene} - {instance} [DENOISE:{tag}]".format(
            scene=scenename, instance=instance.name, tag=backend_tag)
        batch_name = f"{scenename}"

        job_info.Name = job_name
        job_info.BatchName = batch_name
        job_info.Plugin = "CommandLine"

        # Set job priority from settings
        job_priority = denoise_settings.get("priority", 50)
        job_info.Priority = job_priority

        # Set pool from settings
        job_pool = denoise_settings.get("pool", "luma")
        job_info.Pool = job_pool

        # Set group from settings
        job_group = denoise_settings.get("group", "denoise_group")
        job_info.Group = job_group

        # Set job dependencies if provided (should depend on render job)
        if dependency_job_ids:
            job_info.JobDependencies = dependency_job_ids

        # Set frames to match the render frames since denoising is per-frame
        # Use the same frame range as the render job

        job_info.Frames = 1

        # Set output directories and filenames for denoising results
        dirname = os.path.dirname(instance.data["files"][0])
        fname = os.path.basename(instance.data["files"][0])
        # For denoising, output goes to same directory but with _denoised suffix
        # denoised_fname = fname.replace('.exr', '_denoised.exr')
        job_info.OutputDirectory[0] = os.path.join(dirname, 'denoised').replace("\\", "/")
        job_info.OutputFilename[0] = fname

        return job_info

    def get_plugin_info(self, job_type=None):
        instance = self._instance
        addon_settings = (
            instance.context.data["project_settings"]["luma-denoise"])

        plugin_info = CommandLinePluginInfo(
            Executable=self._backend.get_executable(addon_settings),
            Arguments=self._backend.get_arguments(instance, addon_settings),
            SingleFramesOnly=False,
            ShellExecute=False,
            Shell="cmd",
        )
        return asdict(plugin_info)

    def get_denoise_enabled(self, instance):
        """Consolidate denoise logic: settings checking, publish attributes handling, default value assignment."""
        project_settings = instance.context.data["project_settings"]

        # Use only luma-denoise settings
        denoise_settings = project_settings["luma-denoise"].get("denoise", {})

        # Check if denoising is enabled in settings
        if not denoise_settings.get("enabled", False):
            self.log.info("Denoising disabled in 'luma-denoise' settings.")
            return False

        # Get default enabled state from settings
        default_enabled = denoise_settings.get("enabled", True)

        # The denoise value is now set by HoudiniSubmitDeadlineUsdRender plugin
        # Skip if already set in instance.data by the deadline submission
        # (but still need to validate against settings)


        # Fall back to default
        self.log.info(f"Using default denoise value: {default_enabled}")
        return default_enabled

    def process(self, instance):
        self._instance = instance

        try:
            # Consolidate denoise logic and set instance.data["denoise"] if not already set
            if "denoise" not in instance.data:
                instance.data["denoise"] = self.get_denoise_enabled(instance)

            denoise_enabled = instance.data["denoise"]

            if not denoise_enabled:
                return

            # Ensure we have files to denoise
            if not instance.data.get("files"):
                self.log.warning("No files found for denoising.")
                return
        except Exception as e:
            self.log.error(f"Error during denoise initialization: {str(e)}")
            raise

        try:
            # For denoising, we need to depend on the render job, not submit immediately
            # The render job will have already been submitted by the main render plugin
            # We need to find the render job ID and submit our denoise job as dependent

            # Get the render job ID from the instance data (set by the render plugin)
            render_job_id = None
            if "deadlineSubmissionJob" in instance.data:
                submission_job = instance.data["deadlineSubmissionJob"]
                if isinstance(submission_job, dict) and "_id" in submission_job:
                    render_job_id = submission_job["_id"]
                elif hasattr(submission_job, '_id'):
                    render_job_id = submission_job._id

            if not render_job_id:
                self.log.warning("Could not find render job ID for dependency. Skipping denoise submission.")
                return

            # Set up our denoise job with dependency on the render job
            context = instance.context
            self._deadline_url = instance.data["deadline"]["url"]

            assert self._deadline_url, "Requires Deadline Webservice URL"

            # Resolve the denoiser backend from settings and validate config.
            project_settings = context.data["project_settings"]
            addon_settings = project_settings["luma-denoise"]
            backend_name = addon_settings.get(
                "denoise", {}).get("denoiser", "renderman")
            self._backend = get_denoiser_backend(backend_name)
            self._backend.validate(instance, addon_settings)
            instance.data["denoise_backend"] = self._backend.name
            self.log.info(f"Using denoiser backend: {self._backend.name}")

            # Get generic job info and customize for denoising
            job_info = self.get_generic_job_info(instance)
            self.job_info = self.get_job_info(job_info=deepcopy(job_info), dependency_job_ids=[render_job_id])

            # Backend-specific job environment (license, PATH, etc.).
            for key, value in self._backend.get_environment(
                    addon_settings).items():
                self.job_info.EnvironmentKeyValue[key] = value

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

            # Store the denoise job ID for OIIO combine plugin dependency
            instance.data["denoise_job_id"] = job_id

            # Store output directory for unified publisher
            output_dir = os.path.dirname(instance.data["files"][0])
            instance.data["outputDir"] = output_dir
            instance.data["toBeRenderedOn"] = "deadline"

            instance.data["deadline"]["job_info"] = deepcopy(self.job_info)

        except Exception as e:
            self.log.error(f"Error during denoise job submission: {str(e)}")
            import traceback
            self.log.error(traceback.format_exc())
            raise
