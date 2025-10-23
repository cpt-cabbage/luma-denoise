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


class ExtractOiioCombine(
        abstract_submit_deadline.AbstractSubmitDeadline,
        AYONPyblishPluginMixin):
    """Combine denoised and original EXR files using OIIO after denoising is complete.

    This plugin runs oiiotool to merge denoised beauty passes with original AOVs
    into final EXR files for publish.
    """

    label = "Submit OIIO Combine to Deadline"
    order = pyblish.api.ExtractorOrder + 0.1
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

        # Get OIIO settings from project settings
        project_settings = context.data["project_settings"]
        oiio_settings = project_settings.get("luma-denoise", {})

        filepath = context.data["currentFile"]
        scenename = os.path.basename(filepath)
        job_name = "{scene} - {instance} [OIIO COMBINE]".format(scene=scenename, instance=instance.name)
        batch_name = f"{scenename}"

        job_info.Name = job_name
        job_info.BatchName = batch_name
        job_info.Plugin = "CommandLine"

        # Set job priority from settings
        job_priority = oiio_settings.get("combine_deadline_priority", 40)
        job_info.Priority = job_priority

        # Set pool from settings
        job_pool = oiio_settings.get("combine_pool", "luma")
        job_info.Pool = job_pool

        # Set group from settings
        job_group = oiio_settings.get("combine_group", "combine_group")
        job_info.Group = job_group

        # Set job dependencies on denoise jobs
        if dependency_job_ids:
            job_info.JobDependencies = dependency_job_ids

        # Set frames to match the render frames
        start_frame = instance.data.get("frameStartHandle", 1)
        end_frame = instance.data.get("frameEndHandle", 1)
        step = instance.data.get("byFrameStep", 1)
        job_info.Frames = f"{int(start_frame)}-{int(end_frame)}x{int(step)}"

        # Set output directories for combined results
        output_subdirectory = oiio_settings.get("output_subdirectory", "combined")
        if instance.data.get("files"):
            first_file = instance.data["files"][0]
            dirname = os.path.dirname(first_file)
            filename = os.path.basename(first_file)
            shot_name = filename.split('.')[0]
            extension = filename.split('.')[-1]
            combined_dir = os.path.join(dirname, output_subdirectory)
            job_info.OutputDirectory = combined_dir.replace("\\", "/")
            job_info.OutputFilename = f'{shot_name}.<STARTFRAME%4>.{extension}'

        self.log.info(f"Job info configured for OIIO combine: {job_name} (Priority: {job_priority}, Pool: {job_pool})")

        return job_info

    def get_plugin_info(self, job_type=None):
        instance = self._instance
        files = instance.data.get("files", [])

        # Get OIIO settings from project settings
        project_settings = instance.context.data["project_settings"]
        oiio_settings = project_settings.get("luma-denoise", {})

        # Build the executable path from settings
        oiio_root = oiio_settings.get("oiio_root_path", "/opt/oiio")
        oiio_exe_name = oiio_settings.get("oiio_exe", "oiiotool")
        executable_path = os.path.join(oiio_root, "bin", oiio_exe_name).replace("\\", "/")

        # For combining, we need to handle per-frame processing
        if files:
            first_file = files[0]
            dirname = os.path.dirname(first_file)
            basename = os.path.basename(first_file)
            filename = os.path.basename(first_file)
            shot_name = filename.split('.')[0]
            extension = filename.split('.')[-1]

            # Build paths similar to oiio.py
            denoised_path = os.path.join(dirname, 'denoised', f'{shot_name}.<STARTFRAME%4>.{extension}')
            renders_path = os.path.join(dirname, f'{shot_name}.<STARTFRAME%4>.{extension}')
            output_subdirectory = oiio_settings.get("output_subdirectory", "combined")
            output_path = os.path.join(dirname, output_subdirectory, f'{shot_name}.<STARTFRAME%4>.{extension}')

            # Get AOV settings
            include_aovs = oiio_settings.get("include_aovs", True)
            crypto_materials = oiio_settings.get("crypto_materials", True)
            crypto_primitives = oiio_settings.get("crypto_primitives", False)
            diffuse = oiio_settings.get("diffuse", True)
            specular = oiio_settings.get("specular", True)
            albedo = oiio_settings.get("albedo", False)
            normals = oiio_settings.get("normals", True)
            position = oiio_settings.get("position", True)
            uv = oiio_settings.get("uv", False)
            depth = oiio_settings.get("depth", False)

            OIIO_args = ''
            passdict = {}

            if include_aovs:
                OIIO_args += f'"{denoised_path}"'
                OIIO_args += " --ch Beauty.R,Beauty.G,Beauty.B,a.Z"
                if crypto_materials:
                    passdict["CryptoMaterials"] = ["CryptoMaterials.R","CryptoMaterials.G","CryptoMaterials.B"]
                    OIIO_args += ",CryptoMaterials.R,CryptoMaterials.G,CryptoMaterials.B"
                if crypto_primitives:
                    passdict["CryptoPrimitives"] = ["CryptoPrimitives.R","CryptoPrimitives.G","CryptoPrimitives.B"]
                    OIIO_args += ",CryptoPrimitives.R,CryptoPrimitives.G,CryptoPrimitives.B"
                if diffuse:
                    passdict["diffuse"] = ["diffuse.R","diffuse.G","diffuse.B"]
                    OIIO_args += ",diffuse.R,diffuse.G,diffuse.B"
                if specular:
                    passdict["specular"] = ["specular.R","specular.G","specular.B"]
                    OIIO_args += ",specular.R,specular.G,specular.B"
                if albedo:
                    passdict["albedo"] = ["albedo.R","albedo.G","albedo.B"]
                    OIIO_args += ",albedo.R,albedo.G,albedo.B"
                if position:
                    passdict["P"] = ["P.x","P.y","P.z"]
                    OIIO_args += ",P.x,P.y,P.z"
                if uv:
                    passdict["uv"] = ["uv.R","uv.G","uv.B"]
                    OIIO_args += ",uv.R,uv.G,uv.B"
                if depth:
                    passdict["zfiltered"] = ["zfiltered.Z"]
                    OIIO_args += ",zfiltered.Z"
                OIIO_args += f' "{renders_path}"'
                OIIO_args += ' --ch '
                if crypto_materials:
                    OIIO_args += 'CryptoMaterials00.R,CryptoMaterials00.G,CryptoMaterials00.B,CryptoMaterials00.A,CryptoMaterials01.R,CryptoMaterials01.G,CryptoMaterials01.B,CryptoMaterials01.A,CryptoMaterials02.R,CryptoMaterials02.G,CryptoMaterials02.B,CryptoMaterials02.A'
                if crypto_primitives:
                    if crypto_materials:
                        OIIO_args += ','
                    OIIO_args += 'CryptoPrimitives00.R,CryptoPrimitives00.G,CryptoPrimitives00.B,CryptoPrimitives00.A,CryptoPrimitives01.R,CryptoPrimitives01.G,CryptoPrimitives01.B,CryptoPrimitives01.A,CryptoPrimitives02.R,CryptoPrimitives02.G,CryptoPrimitives02.B,CryptoPrimitives02.A'
                if normals:
                    if crypto_materials or crypto_primitives:
                        OIIO_args += ","
                    OIIO_args += "normal.x,normal.y,normal.z"

                OIIO_args += ' --chappend'
                OIIO_args += ' --chnames R,G,B,A'
                if crypto_materials:
                    OIIO_args += ",CryptoMaterials.R,CryptoMaterials.G,CryptoMaterials.B"
                if crypto_primitives:
                    OIIO_args += ",CryptoPrimitives.R,CryptoPrimitives.G,CryptoPrimitives.B"
                if diffuse:
                    OIIO_args += ",diffuse.R,diffuse.G,diffuse.B"
                if specular:
                    OIIO_args += ",specular.R,specular.G,specular.B"
                if albedo:
                    OIIO_args += ",albedo.R,albedo.G,albedo.B"
                if position:
                    OIIO_args += ",P.x,P.y,P.z"
                if uv:
                    OIIO_args += ",uv.R,uv.G,uv.B"
                if depth:
                    OIIO_args += ",zfiltered.Z"
                if crypto_materials:
                    OIIO_args += ',CryptoMaterials00.R,CryptoMaterials00.G,CryptoMaterials00.B,CryptoMaterials00.A,CryptoMaterials01.R,CryptoMaterials01.G,CryptoMaterials01.B,CryptoMaterials01.A,CryptoMaterials02.R,CryptoMaterials02.G,CryptoMaterials02.B,CryptoMaterials02.A'
                if crypto_primitives:
                    OIIO_args += ',CryptoPrimitives00.R,CryptoPrimitives00.G,CryptoPrimitives00.B,CryptoPrimitives00.A,CryptoPrimitives01.R,CryptoPrimitives01.G,CryptoPrimitives01.B,CryptoPrimitives01.A,CryptoPrimitives02.R,CryptoPrimitives02.G,CryptoPrimitives02.B,CryptoPrimitives02.A'
                if normals:
                    OIIO_args += ",normal.x,normal.y,normal.z"
                OIIO_args += f' -o "{output_path}"'

                # Store passdict in instance data for later use
                instance.data["passdict"] = passdict

            else:
                # DEFAULT SUBMISSION
                OIIO_args += f'"{denoised_path}"'
                OIIO_args += " --ch Beauty.R,Beauty.G,Beauty.B,Alpha,CryptoMaterials.R,CryptoMaterials.G,CryptoMaterials.B, "
                OIIO_args += f'"{renders_path}"'
                OIIO_args += ' --ch CryptoMaterials00.R,CryptoMaterials00.G,CryptoMaterials00.B,CryptoMaterials00.A,CryptoMaterials01.R,CryptoMaterials01.G,CryptoMaterials01.B,CryptoMaterials01.A,CryptoMaterials02.R,CryptoMaterials02.G,CryptoMaterials02.B,CryptoMaterials02.A  --chappend --chnames R,G,B,A,CryptoMaterials.R,CryptoMaterials.G,CryptoMaterials.B,CryptoMaterials00.R,CryptoMaterials00.G,CryptoMaterials00.B,CryptoMaterials00.A,CryptoMaterials01.R,CryptoMaterials01.G,CryptoMaterials01.B,CryptoMaterials01.A,CryptoMaterials02.R,CryptoMaterials02.G,CryptoMaterials02.B,CryptoMaterials02.A '
                OIIO_args += f'-o "{output_path}"'

            plugin_info = CommandLinePluginInfo(
                Executable=executable_path,
                Arguments=OIIO_args,
                StartupDirectory=dirname,
                SingleFramesOnly=True  # Each frame is processed independently
            )
        else:
            # Fallback if no files
            plugin_info = CommandLinePluginInfo(
                Executable="echo",
                Arguments="No files to combine",
                SingleFramesOnly=True
            )

        plugin_payload = asdict(plugin_info)
        self.log.info(f"Plugin info configured for OIIO combine: {len(files)} files using {executable_path}")
        return plugin_payload

    def process(self, instance):
        self._instance = instance

        try:
            # Check if OIIO combine is enabled in project settings
            project_settings = instance.context.data["project_settings"]
            oiio_settings = project_settings.get("luma-denoise", {})
            if not oiio_settings.get("oiio_enabled", True):
                self.log.info("OIIO combine disabled in project settings, skipping.")
                return

            # Ensure we have files to combine
            if not instance.data.get("files"):
                self.log.warning("No files found for OIIO combine.")
                return

            # Check for denoising completion - look for denoise job ID in instance data
            denoise_job_id = None
            if "denoise_job_id" in instance.data:
                denoise_job_id = instance.data["denoise_job_id"]
            else:
                self.log.warning("Could not find denoise job ID. Skipping OIIO combine submission.")
                return

            # Set up our combine job with dependency on the denoise job
            context = instance.context
            self._deadline_url = instance.data["deadline"]["url"]

            assert self._deadline_url, "Requires Deadline Webservice URL"

            # Get generic job info and customize for combining
            job_info = self.get_generic_job_info(instance)
            self.job_info = self.get_job_info(job_info=deepcopy(job_info), dependency_job_ids=[denoise_job_id])

            # Set up plugin info for combining
            self.plugin_info = self.get_plugin_info()
            self.aux_files = self.get_aux_files()

            # Apply any additional plugin info data
            plugin_info_data = instance.data["deadline"]["plugin_info_data"]
            if plugin_info_data:
                self.apply_additional_plugin_info(plugin_info_data)

            # Submit the combine job
            job_id = self.process_submission()
            self.log.info(f"Submitted OIIO combine job to Deadline: {job_id} (depends on denoise job {denoise_job_id})")

            # Store output directory for unified publisher
            output_dir = os.path.dirname(instance.data["files"][0])
            instance.data["outputDir"] = output_dir
            instance.data["toBeRenderedOn"] = "deadline"

            instance.data["deadline"]["job_info"] = deepcopy(self.job_info)

        except Exception as e:
            self.log.error(f"Failed to process OIIO combine plugin: {str(e)}")
            raise