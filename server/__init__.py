from typing import Type

from ayon_server.addons import BaseServerAddon
# from ayon_server.api.dependencies import CurrentUser, ProjectName
# from ayon_server.entities import FolderEntity
# from ayon_server.events import EventModel, EventStream
# from ayon_server.exceptions import NotFoundException, NotImplementedException
# from ayon_server.helpers.get_entity_class import get_entity_class
# from ayon_server.lib.postgres import Postgres
# from nxtools import logging

from .settings import LumaDenoiseSettings


class LumaDenoiseAddon(BaseServerAddon):
    settings_model: Type[LumaDenoiseSettings] = LumaDenoiseSettings
    
