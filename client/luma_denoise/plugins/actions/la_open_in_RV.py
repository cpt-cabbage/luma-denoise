import os
import re
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





class OpeninRV(LauncherAction):
    name = "la_open_in_RV"
    label = "Open in RV"
    icon = r"C:\Program Files\OpenRV\resources\RV.ico"
    order = 500


    def runscript(self,project,task,shotpath,shot):


        addons_info =  ayon_api.get_addons_info(True)
        # Find the applications addon
        applications_addon = None
        for addon in addons_info['addons']:
            if addon['name'] == 'applications':
                applications_addon = addon
                break
        applications_addon = next((addon for addon in addons_info['addons'] if addon['name'] == 'applications'),None)
        version = applications_addon['productionVersion']


        print("Addon version: " + str(version))

        exe = ayon_api.get_addon_studio_settings('applications', version).get('applications').get('openrv').get('variants')[-1].get('executables')
        rv_exec = ''

        if sys.platform == "linux":
            rv_exec = str(exe.get('linux')[0])
        if sys.platform == "darwin":
            rv_exec = str(exe.get('darwin')[0])
        if sys.platform == "win32":
            rv_exec = str(exe.get('windows')[0])

        print(rv_exec)
        # Determine the base directory based on task
        shotpath = shotpath.partition(shot)[0] + shot
        print("Shot path in RV: " + str(shotpath))
        base_dir = os.path.join(shotpath, "publish/render")

        print(f"Base directory: {base_dir}")

        if not os.path.exists(base_dir):
            print(f"Directory does not exist: {base_dir}")
            return

        # Get all subdirectories (product folders)
        try:
            product_folders = [f for f in os.listdir(base_dir)
                             if os.path.isdir(os.path.join(base_dir, f))]

            if not product_folders:
                print(f"No product folders found in {base_dir}")
                return

            print(f"Found product folders: {product_folders}")

            # For each product folder, find the latest version
            print('task: ' + task)
            print('product_folders: ' + str(product_folders))
            latest_versions = []
            
            for product_folder in product_folders:
                if task.lower() in product_folder.lower():
                    print("Using latest version for {}".format(task))
                    product_path = os.path.join(base_dir, product_folder)
                    # Get all versions
                    version_folders = [v for v in os.listdir(product_path)
                                    if os.path.isdir(os.path.join(product_path, v))
                                    and v.startswith('v')]
                    if version_folders:
                        # Sort versions and get the latest
                        version_folders.sort()
                        latest_version = version_folders[-1]
                        version_path = os.path.join(product_path, latest_version)

                        # Find EXR files in the version folder
                        exr_files = [f for f in os.listdir(version_path)
                                if f.endswith('.exr')]

                        if exr_files:
                            # Construct the image sequence path
                            # Assuming naming convention like: productName.####.exr
                            first_exr = exr_files[0]  
                            # Replace the frame number with %4d pattern
                            sequence_pattern = first_exr.partition('.')[0] + '.%04d.exr'
                            full_path = os.path.join(version_path, sequence_pattern)
                            latest_versions.append(full_path)

            latest_versions.sort()
            if not latest_versions:
                print("No image sequences found in any version folders")
                return

            # Build RV command with all sequences
            command = latest_versions[0]
            print(f"Opening in RV: {command}")

            subprocess.Popen(f'"{r"C:/Program Files/OpenRV/bin/rv.exe"}" "{command}"', shell=False)

        except Exception as e:
            print(f"Error finding latest versions: {e}")
            import traceback
            traceback.print_exc()
        #W:\LumaRND\shots\ChiefChickenTest\sh0010\publish\render

    def is_compatible(self, selection):

        compatible = False
        if selection.is_task_selected:
                if selection.get_task_name() in ["compositing","lookdev","fx"]:
                    compatible = True          
        return compatible

        
    def process(self, selection, **kwargs):

        project_name = selection.project_name
        folderid = selection.get_folder_id()
        addonssettings = ayon_api.get_addons_project_settings(project_name)
        output_subdirectory = addonssettings.get("output_subdirectory", "combined")
        task_name = selection.get_task_name()
        user = os.environ["USERNAME"]
        shotpath = self._get_workdir(selection)
        # Get path up to and including 'publish'
        print("Shot path: " + str(shotpath))
        parts = shotpath.split(os.sep)
        print("parts: "+ str(parts))
        try:
            shots_index = parts.index('shots')
            for part in parts:
                print("Part: " + part)
                if part.startswith('sh'):
                    shot = part
        except (ValueError, IndexError):
            pass
        print("Shot: " + shot )
        if not shotpath:
            return
        print("Full command:" + f"la_shot_tools.py {project_name} {shot} {task_name} {shotpath} {user} {output_subdirectory}")
        self.runscript(project_name,task_name,shotpath,shot)      

        

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
        if os.path.exists(valid_workdir):
            return valid_workdir

        data.pop("task", None)
        workdir = anatomy.get_template_item(
            "work", "default", "folder"
        ).format(data)
        valid_workdir = self._find_first_filled_path(workdir)
        if valid_workdir:
            # Normalize
            valid_workdir = os.path.normpath(valid_workdir)
            if os.path.exists(valid_workdir):
                return valid_workdir
        raise AssertionError("Folder does not exist yet.")
    


    

import os
import re
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
    icon = r"L:\tools\_studio_tools\la_shot_tools\Icon_white_small.png"
    order = 500



    def runscript(self,project,asset,task,path,user,output_subdirectory):
        subprocess.Popen(r"L:\tools\_studio_tools\la_shot_tools\la_shot_tools.bat {} {} {} {} {} {}".format(project,asset,task,path,user,output_subdirectory)) 



    
    def is_compatible(self, selection):

        compatible = False
        if selection.is_task_selected:
            compatible = True     
        return compatible

        
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
        try:
            shots_index = parts.index('shots')
            for part in parts:
                print("Part: " + part)
                if part.startswith('sh'):
                    shot = part
        except (ValueError, IndexError):
            pass
        print("Shot: " + shot )
        if not shotpath:
            return
        print("Full command:" + f"la_shot_tools.py {project_name} {shot} {task_name} {shotpath} {user} {output_subdirectory}")
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
        if os.path.exists(valid_workdir):
            return valid_workdir

        data.pop("task", None)
        workdir = anatomy.get_template_item(
            "work", "default", "folder"
        ).format(data)
        valid_workdir = self._find_first_filled_path(workdir)
        if valid_workdir:
            # Normalize
            valid_workdir = os.path.normpath(valid_workdir)
            if os.path.exists(valid_workdir):
                return valid_workdir
        raise AssertionError("Folder does not exist yet.")
    


    

