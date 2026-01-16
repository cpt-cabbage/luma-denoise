import os
import subprocess
import ayon_api
import sys
import importlib.util
from string import Formatter
from ayon_api import get_project, get_folder_by_name
from ayon_core.pipeline import (
    Anatomy,
    LauncherAction,
)
from ayon_core.pipeline.template_data import get_template_data





class StartshotTools(LauncherAction):
    name = "la_shot_tools"
    label = "Luma Tools"
    icon = r"resources\Icon_white_small.png"
    order = 500



    def runscript(self,project,asset,task,path,user,output_subdirectory):
        subprocess.Popen(r"L:\tools\_studio_tools\AYON\_dev\christophe\la_shot_tools\luma_tools\luma_tools.bat {} {} {} {} {} {}".format(project,asset,task,path,user,output_subdirectory))





    def is_compatible(self, selection):
        # Only show on shot folders
        folder_path = getattr(selection, "_folder_path", None)
        if folder_path:
            folder_name = folder_path.rsplit("/", 1)[-1]
            if not folder_name.startswith("sh"):
                return False
        return True


    def process(self, selection, **kwargs):

        project_name = selection.project_name
        addonssettings = ayon_api.get_addons_project_settings(project_name)
        output_subdirectory = addonssettings.get("output_subdirectory", "combined")
        task_name = selection.get_task_name()
        user = os.environ["USERNAME"]
        shotpath = self._get_workdir(selection)
        # Get path up to and including 'work'
        shotpath = shotpath.partition('work')[0] + 'work'
        print("Shot path: " + str(shotpath))
        parts = shotpath.split(os.sep)
        print("parts: "+ str(parts))
        shot = None
        try:
            shots_index = parts.index('shots')
            for part in parts:
                print("Part: " + part)
                if part.startswith('sh'):
                    shot = part
        except (ValueError, IndexError):
            pass
        print("Shot: " + str(shot))
        if not shotpath:
            return
        self.runscript(project_name,shot,task_name,shotpath,user,output_subdirectory)




    def _find_first_filled_path(self, path):
        if not path:
            return ""

        fields = set()
        for item in Formatter().parse(path):
            _, field_name, format_spec, conversion = item
            if not field_name:
                continue
            conversion = "!{}".format(conversion) if conversion else ""
            format_spec = ":{}".format(format_spec) if format_spec else ""
            orig_key = "{{{}{}{}}}".format(
                field_name, conversion, format_spec)
            fields.add(orig_key)

        for field in fields:
            path = path.split(field, 1)[0]
        return path

    def _get_workdir(self, selection):
        data = get_template_data(
            selection.project_entity,
            selection.folder_entity,
            selection.task_entity
        )

        anatomy = Anatomy(
            selection.project_name,
            project_entity=selection.project_entity
        )
        workdir = anatomy.get_template_item(
            "work", "default", "folder"
        ).format(data)

        # Remove any potential un-formatted parts of the path
        valid_workdir = self._find_first_filled_path(workdir)

        # Path is not filled at all
        if not valid_workdir:
            raise AssertionError("Failed to calculate workdir.")

        # Normalize
        valid_workdir = os.path.normpath(valid_workdir)
        return valid_workdir