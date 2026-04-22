import os
from copy import deepcopy
from dataclasses import dataclass, field, asdict

import pyblish.api

from ayon_core.pipeline import AYONPyblishPluginMixin
from ayon_deadline import abstract_submit_deadline


# Default exclude patterns mirror the server settings default. Kept here as a
# safety net in case the settings entry is missing from project bundles.
DEFAULT_EXCLUDE_PATTERNS: list[str] = ["*_mse", "mse", "sampleCount"]

# Default rename maps. Settings override these per-project.
DEFAULT_RENAME_DENOISED: list[dict[str, str]] = [
    {"source": "Ci.r", "target": "R"},
    {"source": "Ci.g", "target": "G"},
    {"source": "Ci.b", "target": "B"},
    {"source": "a.Z", "target": "A"},
]
DEFAULT_RENAME_RAW: list[dict[str, str]] = [
    {"source": "beauty.r", "target": "R"},
    {"source": "beauty.g", "target": "G"},
    {"source": "beauty.b", "target": "B"},
    {"source": "a.Z", "target": "A"},
]


@dataclass
class CommandLinePluginInfo:
    Executable: str = field(default=None)
    Arguments: str = field(default=None)
    StartupDirectory: str = field(default=None)
    SingleFramesOnly: bool = field(default=False)
    ShellExecute: bool = field(default=False)
    Shell: str = field(default=None)


class ExtractOiioCombine(
        abstract_submit_deadline.AbstractSubmitDeadline,
        AYONPyblishPluginMixin):
    """Submit a Deadline CommandLine job that runs oiio_combine.py per frame.

    The wrapper script reads channel lists of the denoised and raw EXRs at
    runtime, computes the set-difference minus exclude patterns, renames the
    beauty layer to R/G/B/A (Nuke convention), and invokes oiiotool to emit
    the final combined EXR.

    Stubs filled in at submit time:
      - dependency (denoise job, or render job in pass-through)
      - file paths and frame-token substitutions
      - setting-driven wrapper CLI flags

    Channel selection and rename logic lives in the wrapper script, not here.
    """

    label = "Submit OIIO Combine to Deadline"
    order = pyblish.api.IntegratorOrder + 0.11
    hosts = ["houdini"]
    families = ["usdrender"]
    targets = ["local"]

    def get_job_info(self, dependency_job_ids=None, job_info=None):
        instance = self._instance
        context = instance.context

        assert all(
            result["success"] for result in context.data["results"]
        ), "Errors found, aborting integration.."

        project_settings = context.data["project_settings"]
        oiio_settings = project_settings.get("luma-denoise", {})

        filepath = context.data["currentFile"]
        scenename = os.path.basename(filepath)
        job_name = "{scene} - {instance} [OIIO COMBINE]".format(
            scene=scenename, instance=instance.name)
        batch_name = f"{scenename}"

        job_info.Name = job_name
        job_info.BatchName = batch_name
        job_info.Plugin = "CommandLine"
        job_info.Priority = oiio_settings.get("combine_deadline_priority", 40)
        job_info.Pool = oiio_settings.get("combine_pool", "luma")
        job_info.Group = oiio_settings.get("combine_group", "combine_group")

        if dependency_job_ids:
            job_info.JobDependencies = dependency_job_ids

        publish_attrs = instance.data.get("publish_attributes", {})
        jobinfo_attrs = publish_attrs.get("CollectJobInfo", {})
        use_custom = jobinfo_attrs.get("use_custom_frames", "none")
        custom_frames_str = jobinfo_attrs.get("frames", "")

        if use_custom in ("custom_only", "reuse_last_version") and custom_frames_str:
            job_info.Frames = custom_frames_str
        else:
            start_frame = instance.data.get("frameStartHandle", 1)
            end_frame = instance.data.get("frameEndHandle", 1)
            step = instance.data.get("byFrameStep", 1)
            job_info.Frames = f"{int(start_frame)}-{int(end_frame)}x{int(step)}"

        output_subdirectory = oiio_settings.get("output_subdirectory", "combined")
        if instance.data.get("files"):
            first_file = instance.data["files"][0]
            dirname = os.path.dirname(first_file)
            filename = os.path.basename(first_file)
            shot_name = filename.split('.')[0]
            extension = filename.split('.')[-1]
            combined_dir = os.path.join(dirname, output_subdirectory)
            os.makedirs(combined_dir, exist_ok=True)
            job_info.OutputDirectory[0] = combined_dir.replace("\\", "/")
            job_info.OutputFilename[0] = f'{shot_name}.<STARTFRAME%4>.{extension}'

        return job_info

    def get_plugin_info(self, job_type=None):
        instance = self._instance
        files = instance.data.get("files", [])

        if not files:
            return asdict(CommandLinePluginInfo(
                Executable="echo",
                Arguments="No files to combine",
                SingleFramesOnly=True,
                ShellExecute=False,
                Shell="cmd",
            ))

        project_settings = instance.context.data["project_settings"]
        oiio_settings = project_settings.get("luma-denoise", {})

        oiio_root = oiio_settings.get(
            "oiio_root_path",
            r"L:\tools\_studio_tools\AYON\AYON-1.3.3-windows"
            r"\addons_resources\ayon_third_party\oiio_windows_83e412e9",
        )
        oiiotool_path = os.path.join(oiio_root, "bin", "oiiotool.exe")

        first_file = files[0]
        dirname = os.path.dirname(first_file)
        filename = os.path.basename(first_file)
        shot_name = filename.split('.')[0]
        extension = filename.split('.')[-1]

        renders_path = os.path.join(
            dirname, f'{shot_name}.<STARTFRAME%4>.{extension}')
        output_subdirectory = oiio_settings.get("output_subdirectory", "combined")
        output_path = os.path.join(
            dirname, output_subdirectory,
            f'{shot_name}.<STARTFRAME%4>.{extension}')

        denoise_enabled = instance.data.get("denoise", False)
        denoise_ran = denoise_enabled and "denoise_job_id" in instance.data

        if denoise_ran:
            denoised_path = os.path.join(
                dirname, 'denoised',
                f'{shot_name}.<STARTFRAME%4>.{extension}')
            rename_pairs_cfg = oiio_settings.get(
                "beauty_rename_map_denoised", DEFAULT_RENAME_DENOISED)
        else:
            denoised_path = renders_path
            rename_pairs_cfg = oiio_settings.get(
                "beauty_rename_map_raw", DEFAULT_RENAME_RAW)

        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        python_exe = oiio_settings.get("python_executable", "python")
        default_excludes = list(DEFAULT_EXCLUDE_PATTERNS)
        user_excludes = list(oiio_settings.get(
            "channel_exclude_patterns", default_excludes))
        # If user hasn't customized, user_excludes == default_excludes — dedupe.
        if user_excludes == default_excludes:
            all_excludes = default_excludes
            num_defaults = len(default_excludes)
        else:
            # Pass defaults first, then user. Duplicates are harmless for
            # filtering; the manifest will correctly label the split.
            all_excludes = default_excludes + [
                p for p in user_excludes if p not in default_excludes
            ]
            num_defaults = len(default_excludes)

        extra_args = oiio_settings.get("oiiotool_extra_args", "")
        compression = oiio_settings.get("output_compression", "zips")
        data_type = oiio_settings.get("output_data_type", "preserve")
        write_manifest = oiio_settings.get("write_combine_manifest", True)
        verbose = oiio_settings.get("wrapper_verbose_logging", True)

        parts: list[str] = []
        parts.append("oiio_combine.py")
        parts.extend(["--denoised", self._quote(denoised_path)])
        parts.extend(["--raw", self._quote(renders_path)])
        parts.extend(["--output", self._quote(output_path)])
        parts.extend(["--oiiotool", self._quote(oiiotool_path)])
        for pat in all_excludes:
            parts.extend(["--exclude", self._quote(pat)])
        parts.extend(["--num-default-excludes", str(num_defaults)])
        for pair in rename_pairs_cfg:
            src = pair.get("source") if isinstance(pair, dict) else getattr(pair, "source", "")
            dst = pair.get("target") if isinstance(pair, dict) else getattr(pair, "target", "")
            if src and dst:
                parts.extend(["--rename", self._quote(f"{src}={dst}")])
        if extra_args:
            parts.extend(["--extra-args", self._quote(extra_args)])
        if compression:
            parts.extend(["--compression", self._quote(compression)])
        parts.extend(["--data-type", data_type])
        if write_manifest:
            parts.append("--write-manifest")
        if verbose:
            parts.append("--verbose")

        arguments = " ".join(parts)

        return asdict(CommandLinePluginInfo(
            Executable=python_exe,
            Arguments=arguments,
            StartupDirectory=None,
            SingleFramesOnly=True,
            ShellExecute=False,
            Shell="cmd",
        ))

    @staticmethod
    def _quote(value: str) -> str:
        """Wrap a value in double quotes if it contains spaces."""
        value = str(value)
        if " " in value and not (value.startswith('"') and value.endswith('"')):
            return f'"{value}"'
        return value

    def get_aux_files(self):
        """Deliver the wrapper script to the Deadline worker.

        Returns:
            list[str]: Single-element list with the absolute path to
                oiio_combine.py. Deadline's AuxFiles mechanism copies it into
                the worker's job directory where `python oiio_combine.py` can
                resolve it.
        """
        script_path = os.path.join(
            os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.dirname(__file__)))),
            "scripts", "oiio_combine.py",
        )
        return [script_path.replace("\\", "/")]

    def process(self, instance):
        self._instance = instance

        try:
            project_settings = instance.context.data["project_settings"]
            oiio_settings = project_settings.get("luma-denoise", {})

            if not oiio_settings.get("oiio_enabled", True):
                self.log.info("OIIO combine disabled in project settings, skipping.")
                return

            if not instance.data.get("files"):
                self.log.warning("No files found for OIIO combine.")
                return

            denoise_job_id = instance.data.get("denoise_job_id")
            run_when_no_denoise = oiio_settings.get("run_when_denoise_disabled", False)

            if denoise_job_id:
                dependency_job_id = denoise_job_id
                self.log.info(
                    f"OIIO combine depends on denoise job: {denoise_job_id}")
            else:
                if not run_when_no_denoise:
                    self.log.info(
                        "Denoise did not run and run_when_denoise_disabled is False — "
                        "skipping OIIO combine.")
                    return

                render_job_id = None
                if "deadlineSubmissionJob" in instance.data:
                    submission_job = instance.data["deadlineSubmissionJob"]
                    if isinstance(submission_job, dict) and "_id" in submission_job:
                        render_job_id = submission_job["_id"]
                    elif hasattr(submission_job, '_id'):
                        render_job_id = submission_job._id

                if not render_job_id:
                    self.log.warning(
                        "No denoise or render job ID found. Skipping OIIO combine.")
                    return

                dependency_job_id = render_job_id
                self.log.info(
                    f"OIIO combine depends on render job: {render_job_id} "
                    "(pass-through, no denoise)")

            self._deadline_url = instance.data["deadline"]["url"]
            assert self._deadline_url, "Requires Deadline Webservice URL"

            job_info = self.get_generic_job_info(instance)
            self.job_info = self.get_job_info(
                job_info=deepcopy(job_info),
                dependency_job_ids=[dependency_job_id])

            self.plugin_info = self.get_plugin_info()
            self.aux_files = self.get_aux_files()

            plugin_info_data = instance.data["deadline"]["plugin_info_data"]
            if plugin_info_data:
                self.apply_additional_plugin_info(plugin_info_data)

            job_id = self.process_submission()
            self.log.info(
                f"Submitted OIIO combine job to Deadline: {job_id} "
                f"(depends on {dependency_job_id})")

            instance.data["oiio_combine_job_id"] = job_id

            output_dir = os.path.dirname(instance.data["files"][0])
            instance.data["outputDir"] = output_dir
            instance.data["toBeRenderedOn"] = "deadline"
            instance.data["stagingDir"] = oiio_settings.get("output_subdirectory", "combined")

            instance.data["deadline"]["job_info"] = deepcopy(self.job_info)

        except Exception as e:
            self.log.error(f"Failed to process OIIO combine plugin: {str(e)}")
            raise
