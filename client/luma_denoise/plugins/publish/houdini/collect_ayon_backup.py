"""Backup and restore AYON instance parameters via HDA userData.

Editable nodes inside HDAs can lose their spare parameter values when
the HDA definition is updated or reloaded. This plugin protects against
that by mirroring the AYON instance parms into hou.Node.setUserData()
on the parent HDA, which always saves reliably in the hip file.

Flow:
    1. On collect (early): If the ROP's parms look empty/default but the
       HDA has a userData backup, restore the values to the ROP.
    2. On collect (late): Back up the current ROP parm values to the
       HDA's userData for next time.
"""

import json
import hou
import pyblish.api

from ayon_houdini.api import plugin


# Parameters that AYON manages on instance nodes
AYON_PARMS = [
    "id",
    "productType",
    "active",
    "creator_identifier",
    "variant",
    "folderPath",
    "task",
    "creator_attributes",
    "publish_attributes",
    "AYON_productName",
]

# Key used for hou.Node.setUserData on the HDA
USERDATA_KEY = "ayon_instance_backup"

# Relative path from HDA root to the internal ROP
INTERNAL_ROP_PATH = "ropnet/AYON_ASSET"


def _find_hda_rop_pairs():
    """Find all HDA nodes in /stage that contain an AYON editable ROP."""
    stage = hou.node("/stage")
    if not stage:
        return

    for node in stage.allSubChildren():
        # Must be an HDA
        if not node.type().definition():
            continue

        # Must contain our internal ROP
        rop = node.node(INTERNAL_ROP_PATH)
        if not rop:
            continue

        # The ROP must be an AYON instance
        id_parm = rop.parm("id")
        if not id_parm:
            continue
        if id_parm.eval() != "ayon.create.instance":
            continue

        yield node, rop


def _read_rop_parms(rop):
    """Read AYON parm values from the ROP into a dict."""
    data = {}
    for parm_name in AYON_PARMS:
        parm = rop.parm(parm_name)
        if parm:
            data[parm_name] = parm.eval()
    return data


def _rop_parms_look_empty(rop):
    """Check if the ROP's AYON parms appear to have been wiped.

    After an HDA definition update, editable node spare parms may
    revert to defaults or disappear entirely.
    """
    id_parm = rop.parm("id")
    if not id_parm:
        # Spare parms are completely gone
        return True

    # If critical parms are empty/default, consider it wiped
    folder_path = rop.parm("folderPath")
    if folder_path and not folder_path.eval():
        variant = rop.parm("variant")
        if variant and not variant.eval():
            return True

    return False


def _restore_to_rop(rop, data, log):
    """Write backed-up values to the ROP's parms."""
    for parm_name, value in data.items():
        parm = rop.parm(parm_name)
        if parm:
            try:
                parm.set(value)
            except Exception as e:
                log.warning(
                    f"Could not restore parm '{parm_name}' on "
                    f"'{rop.path()}': {e}"
                )


class CollectRestoreAyonBackup(plugin.HoudiniContextPlugin):
    """Restore AYON instance parms from HDA userData if they look wiped.

    Runs early before AYON's own collectors read the parm values.
    """

    label = "Restore AYON Instance Backup"
    order = pyblish.api.CollectorOrder - 0.49

    def process(self, context):
        restored = 0
        for hda_node, rop in _find_hda_rop_pairs():
            if not _rop_parms_look_empty(rop):
                continue

            # Try to restore from userData
            backup_json = hda_node.userData(USERDATA_KEY)
            if not backup_json:
                self.log.warning(
                    f"ROP parms look empty on '{rop.path()}' but no "
                    f"backup found on '{hda_node.path()}'."
                )
                continue

            try:
                data = json.loads(backup_json)
            except json.JSONDecodeError:
                self.log.warning(
                    f"Corrupt backup data on '{hda_node.path()}', skipping."
                )
                continue

            _restore_to_rop(rop, data, self.log)
            self.log.info(
                f"Restored AYON parms from backup: "
                f"{hda_node.path()} -> {rop.path()}"
            )
            restored += 1

        if restored:
            self.log.info(f"Restored {restored} instance(s) from backup.")


class CollectBackupAyonParms(plugin.HoudiniContextPlugin):
    """Back up AYON instance parms from ROP to HDA userData.

    Runs late in collection after AYON has finished reading/writing
    parm values, so the backup reflects the latest state.
    """

    label = "Backup AYON Instance Parameters"
    order = pyblish.api.CollectorOrder + 0.49

    def process(self, context):
        backed_up = 0
        for hda_node, rop in _find_hda_rop_pairs():
            data = _read_rop_parms(rop)
            if not data:
                continue

            # Only back up if the data looks valid
            if not data.get("id") == "ayon.create.instance":
                continue

            backup_json = json.dumps(data)
            hda_node.setUserData(USERDATA_KEY, backup_json)

            self.log.debug(
                f"Backed up AYON parms: {rop.path()} -> "
                f"{hda_node.path()} userData"
            )
            backed_up += 1

        if backed_up:
            self.log.info(f"Backed up {backed_up} instance(s).")
