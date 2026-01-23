import os
import subprocess
import ayon_api
import sys
import importlib.util
import json
from string import Formatter
from ayon_api import get_project, get_folder_by_name
from ayon_core.pipeline import (
    Anatomy,
    LauncherAction,
)
from ayon_core.pipeline.template_data import get_template_data
from qtpy import QtWidgets
from qtpy.QtCore import Qt

# Config file path for persistent settings
_CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".luma_tools_config.json")


def load_dev_mode():
    """Load dev mode setting from config file."""
    try:
        if os.path.exists(_CONFIG_FILE):
            with open(_CONFIG_FILE, "r") as f:
                config = json.load(f)
                return config.get("dev_mode", False)
    except Exception:
        pass
    return False


def save_dev_mode(enabled):
    """Save dev mode setting to config file."""
    try:
        with open(_CONFIG_FILE, "w") as f:
            json.dump({"dev_mode": enabled}, f)
    except Exception:
        pass


# Load persistent dev mode state on module load
_dev_mode_enabled = load_dev_mode()


def is_ctrl_held():
    """Check if Ctrl key is currently held."""
    modifiers = QtWidgets.QApplication.keyboardModifiers()
    return bool(modifiers & Qt.ControlModifier)


def show_settings_dialog():
    """Show settings dialog with dev mode toggle checkbox."""
    global _dev_mode_enabled

    # Re-read from config file to get latest value
    _dev_mode_enabled = load_dev_mode()

    dialog = QtWidgets.QDialog()
    dialog.setWindowTitle("Luma Tools Settings")
    dialog.setMinimumWidth(250)

    layout = QtWidgets.QVBoxLayout(dialog)

    # Dev mode checkbox
    checkbox = QtWidgets.QCheckBox("Dev Mode")
    checkbox.setChecked(_dev_mode_enabled)
    layout.addWidget(checkbox)

    layout.addSpacing(10)

    # Buttons
    button_layout = QtWidgets.QHBoxLayout()
    ok_btn = QtWidgets.QPushButton("OK")
    cancel_btn = QtWidgets.QPushButton("Cancel")

    ok_btn.clicked.connect(dialog.accept)
    cancel_btn.clicked.connect(dialog.reject)

    button_layout.addStretch()
    button_layout.addWidget(ok_btn)
    button_layout.addWidget(cancel_btn)

    layout.addLayout(button_layout)

    result = dialog.exec_()

    if result == QtWidgets.QDialog.Accepted:
        _dev_mode_enabled = checkbox.isChecked()
        save_dev_mode(_dev_mode_enabled)

    return _dev_mode_enabled


class StartshotTools(LauncherAction):
    name = "la_shot_tools"
    label = "Luma Tools"
    icon = r"L:\tools\_studio_tools\luma_tools\resources\Icon_white_small.png"
    order = 500

    def runscript(self, project, asset, task, path, user, output_subdirectory, dev_mode):
        # Ensure all arguments are strings (convert None to empty string)
        args = [str(arg) if arg is not None else "" for arg in [project, asset, task, path, user, output_subdirectory]]
        settings_variant = ayon_api.get_default_settings_variant()

        if dev_mode:
            base_path = r"L:\tools\_studio_tools\AYON\_dev\christophe\la_shot_tools\luma_tools"
        else:
            base_path = r"L:\tools\_studio_tools\luma_tools"

        if os.name == 'nt':  # Windows
            python_path = os.path.join(base_path, "python", "venv", "Scripts", "python.exe")
            script_path = os.path.join(base_path, "python", "core", "luma_tools.py")
            env = os.environ.copy()
            env["PYTHONPATH"] = os.path.join(base_path, "python") + ";" + os.path.join(base_path, "resources", "ui") + ";" + env.get("PYTHONPATH", "")
            env["AYON_DEFAULT_SETTINGS_VARIANT"] = settings_variant
            # Use cmd /k to keep window open after script exits, CREATE_NEW_CONSOLE to show window
            args_str = " ".join('"{}"'.format(a) for a in args)
            # Debug: echo the arguments before running
            cmd = 'cmd /k echo Args: {} && "{}" "{}" {}'.format(args_str, python_path, script_path, args_str)
            subprocess.Popen(cmd, env=env, cwd=base_path, creationflags=subprocess.CREATE_NEW_CONSOLE)
        else:  # macOS / Linux
            base_path = "/Volumes/libraries/_studio_tools/luma_tools"
            python_path = os.path.join(base_path, "python", "venv", "bin", "python")
            script_path = os.path.join(base_path, "python", "core", "luma_tools.py")
            env = os.environ.copy()
            env["PYTHONPATH"] = os.path.join(base_path, "python") + ":" + os.path.join(base_path, "resources", "ui") + ":" + env.get("PYTHONPATH", "")
            env["AYON_DEFAULT_SETTINGS_VARIANT"] = settings_variant
            subprocess.Popen([python_path, script_path] + args, env=env, cwd=base_path)

    def is_compatible(self, selection):
        # Only show on shot folders
        folder_path = getattr(selection, "_folder_path", None)
        if folder_path:
            folder_name = folder_path.rsplit("/", 1)[-1]
            if not folder_name.startswith("sh"):
                return False
        return True

    def process(self, selection, **kwargs):
        global _dev_mode_enabled

        # Ctrl+click shows settings dialog
        if is_ctrl_held():
            show_settings_dialog()
            return  # Don't run, just change settings

        # Normal click runs with current mode
        mode_str = "DEV" if _dev_mode_enabled else "PRODUCTION"
        print(f"Luma Tools: Running in {mode_str} mode (Ctrl+click to change)")

        project_name = selection.project_name
        if not project_name:
            QtWidgets.QMessageBox.warning(
                None,
                "Luma Tools",
                "No project selected. Please select a project first."
            )
            return
        addonssettings = ayon_api.get_addons_project_settings(project_name)
        output_subdirectory = addonssettings.get("output_subdirectory", "combined")
        task_name = selection.get_task_name()
        user = os.environ.get("USER") or os.environ.get("USERNAME")
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
        self.runscript(project_name, shot, task_name, shotpath, user, output_subdirectory, _dev_mode_enabled)

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
        if not os.path.exists(valid_workdir):
            os.makedirs(valid_workdir)
        return valid_workdir
