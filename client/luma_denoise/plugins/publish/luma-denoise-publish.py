import os
from copy import deepcopy
from dataclasses import dataclass, field, asdict
import pyblish.api

from ayon_core.pipeline import AYONPyblishPluginMixin
from ayon_deadline import abstract_submit_deadline

from ayon_houdini.api import plugin
from ayon_houdini.api.action import SelectROPAction
from ayon_houdini.api.usd import (
    get_usd_rop_loppath,
    get_usd_render_rop_rendersettings)

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
        denoise_settings = project_settings["luma-denoise"]

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
        job_pool = denoise_settings.get("denoise_pool", "luma")
        job_info.Pool = job_pool

        # Set group from settings
        job_group = denoise_settings.get("denoise_group", "denoise_group")
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
        files = instance.data["files"]

        # Get denoise settings from project settings
        project_settings = instance.context.data["project_settings"]
        denoise_settings = project_settings["luma-denoise"]

        # Build the executable path from settings
        rman_root = denoise_settings.get("denoise_rmantree_path", "/opt/pixar/RenderManProServer-26.3")
        denoise_exe_name = denoise_settings.get("denoise_exe", "denoise_batch")
        executable_path = os.path.join(rman_root, "bin", denoise_exe_name).replace("\\", "/")

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
            # frame_pattern = basename.replace('.####.exr', '.<STARTFRAME>.exr')
            # output_pattern = basename.replace('.####.exr', '_denoised.<STARTFRAME>.exr')

            # Build the denoise command arguments using Deadline frame substitution
            start_frame = instance.data.get("frameStartHandle", 1)
            end_frame = instance.data.get("frameEndHandle", 1)
            length = end_frame - start_frame + 1
            # Tile based on resolution
            resx, resy, pixel_aspect = self.get_expected_resolution(instance)
            tile_amntx = 2
            tile_amnty = 2

            args = r' -a 0'
            args += ' -v'
            args += ' --clean-alpha'
            args += ' --progress'
            if length >= 8:
                self.log.debug("8 or more files detected, enabling cross-frame denoising.")
                args += ' -cf'
            else:
                self.log.debug("Less than 8 files detected, using single-frame denoising.")
            # Check for large image to enable tiled denoising for pcs with less than 96GB RAM
            # TODO: Make this configurable in settings
            if self.detectlargeimage(instance):
                self.log.info("Large image detected, enabling tiled denoising.")
                args += ' --tiles {} {} '.format(str(tile_amntx),str(tile_amnty))
            args += r' -o ' + os.path.join(dirname, 'denoised').replace("\\", "/")
            args += ' ' + os.path.join(dirname, basename).replace("\\", "/")
            args += ' ' + str(instance.data.get("frameStartHandle", 1)) + '-' + str(instance.data.get("frameEndHandle", 1))

            # arguments = f'--input "{os.path.join(dirname, frame_pattern)}" --output "{os.path.join(dirname, output_pattern)}"'

            plugin_info = CommandLinePluginInfo(
                Executable=executable_path,
                Arguments=args,
                # StartupDirectory=dirname.replace("\\", "/"),
                SingleFramesOnly=False,
                ShellExecute=False,
                Shell="cmd"  # Each frame is processed independently
            )
        else:
            # Fallback if no files
            plugin_info = CommandLinePluginInfo(
                Executable="echo",
                Arguments="No files to denoise",
                SingleFramesOnly=False,
                ShellExecute=False,
                Shell="cmd" 
            )

        plugin_payload = asdict(plugin_info)
        return plugin_payload

    def get_denoise_enabled(self, instance):
        """Consolidate denoise logic: settings checking, publish attributes handling, default value assignment."""
        project_settings = instance.context.data["project_settings"]

        # Use only luma-denoise settings
        denoise_settings = project_settings["luma-denoise"]

        # Check if denoising is enabled in settings
        if not denoise_settings.get("denoise_enabled", False):
            self.log.info("Denoising disabled in 'luma-denoise' settings.")
            return False

        # Get default enabled state from settings
        default_enabled = denoise_settings.get("denoise_enabled", True)

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
            self.log.info(f"Denoise enabled for instance: {denoise_enabled}")

            if not denoise_enabled:
                self.log.info("Denoising disabled for this instance, skipping.")
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

            # Get generic job info and customize for denoising
            job_info = self.get_generic_job_info(instance)
            self.job_info = self.get_job_info(job_info=deepcopy(job_info), dependency_job_ids=[render_job_id])

            # Get project settings for denoise configuration
            project_settings = context.data["project_settings"]
            denoise_settings = project_settings["luma-denoise"]

            # Add Pixar license environment variable from settings
            pixar_license = denoise_settings.get("denoise_pixar_lic", "")
            if pixar_license:
                self.job_info.EnvironmentKeyValue["PIXAR_LICENSE_FILE"] = pixar_license

            # Add PATH environment variable from settings
            rmPATH = denoise_settings.get("denoise_rmantree_path", "") + R"/bin"
            if rmPATH:
                self.job_info.EnvironmentKeyValue["PATH"] = rmPATH

            # Add RMANTREE environment variable from settings
            rmTREE = denoise_settings.get("denoise_rmantree_path", "")
            if rmTREE:
                self.job_info.EnvironmentKeyValue["RMANTREE"] = rmTREE

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
    
    @classmethod
    def detectlargeimage(self,instance):
        # Import Houdini modules only when this method is called
        import hou
        from pxr import Usd, UsdRender

        # Get render resolution and pixel aspect ratio from USD stage
        rop_node = hou.node(instance.data["instance_node"])
        lop_node: hou.LopNode = get_usd_rop_loppath(rop_node)
        if not lop_node:
            self.log.debug(
                f"No LOP node found for ROP node: {rop_node.path()}")
            return False

        stage: Usd.Stage = lop_node.stage()
        render_settings: UsdRender.Settings = (
            get_usd_render_rop_rendersettings(rop_node, stage, logger=self.log))
        if not render_settings:
            return False

        invalid = []

        # Each render product can have different resolution set if explicitly
        # overridden. If not set, it will use the resolution from the render
        # settings.
        sample_time = Usd.TimeCode.EarliestTime()

        # Get all resolution and pixel aspect attributes to validate
        resolution_attributes = [render_settings.GetResolutionAttr()]
        pixel_aspect_attributes = [render_settings.GetPixelAspectRatioAttr()]
        for product in self.iter_render_products(render_settings, stage):
            resolution_attr = product.GetResolutionAttr()
            if resolution_attr.HasAuthoredValue():
                resolution_attributes.append(resolution_attr)

            pixel_aspect_attr = product.GetPixelAspectRatioAttr()
            if pixel_aspect_attr.HasAuthoredValue():
                pixel_aspect_attributes.append(pixel_aspect_attr)

        # Validate resolution and pixel aspect ratio
        # width, height, pixel_aspect = resolution_attributes
          # Get and print the actual resolution values
        for res_attr in resolution_attributes:
            resolution = res_attr.Get(sample_time)
            self.log.info(f"Resolution: {resolution}")
        # self.log.debug(f"Width: {width}, Height: {height}")
        if resolution[0] >= 2048 or resolution[1] >= 2048:
            return True
        # return False

    @classmethod
    def get_expected_resolution(self, instance):
        """Return the expected resolution and pixel aspect ratio for the
        instance based on the task entity or folder entity."""

        entity = instance.data.get("taskEntity")
        if not entity:
            entity = instance.data["folderEntity"]

        attributes = entity["attrib"]
        width = attributes["resolutionWidth"]
        height = attributes["resolutionHeight"]
        pixel_aspect = attributes["pixelAspect"]
        return int(width), int(height), float(pixel_aspect) 
    
    @classmethod
    def iter_render_products(self, render_settings, stage):
        """Iterate over all render products in the USD render settings"""
        # Import USD modules only when this method is called
        from pxr import UsdRender

        for product_path in render_settings.GetProductsRel().GetTargets():
            prim = stage.GetPrimAtPath(product_path)
            if not prim.IsValid():
                self.log.debug(
                    f"Render product path is not a valid prim: {product_path}")
                return

            if prim.IsA(UsdRender.Product):
                yield UsdRender.Product(prim)






    

