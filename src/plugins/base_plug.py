"""Copyright 2023 Luca Cilloni

   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at

       https://www.apache.org/licenses/LICENSE-2.0

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.
"""

# Modules
import copy
import logging
import threading
from typing import Any
from typing_extensions import Self
from dataclasses import dataclass
from abc import ABC, abstractmethod


# Local Modules
import src.common_types as common_types
import src.gnmi_pb2.gnmi_pb2 as gnmi_pb2
from src.exporter.promexp import PromClient


class _GnmiMessage:
    def __init__(self, nf: gnmi_pb2.Notification):
        self.timestamp = nf.timestamp
        self.atomic = nf.atomic
        self.path: list[str] = []
        self.path_keys: list[dict[str, str]] = []

        self.scan_gnmi_path(nf.prefix)

    def scan_gnmi_path(self, path: gnmi_pb2.Path) -> None:
        for pe in path.elem:
            self.path.append(pe.name)
            self.path_keys.append(dict(pe.key))

    def get_path_key(self, index: int, name: str) -> str:
        try:
            return self.path_keys[index][name]
        except (IndexError, KeyError):
            logging.error(f"The required path key is not available.")
            return 'not_available'


class GnmiUpdate(_GnmiMessage):
    def __init__(self, nf: gnmi_pb2.Notification):
        super().__init__(nf)
        self.val: Any = None
        self.duplicates: int = 0

    def new_upd_msg(self, upd_msg: gnmi_pb2.Update) -> Self:
        msg = copy.deepcopy(self)
        msg.scan_gnmi_path(upd_msg.path)
        msg.val = getattr(upd_msg.val, upd_msg.val.WhichOneof('value'))
        msg.duplicates = upd_msg.duplicates
        return msg


class GnmiDelete(_GnmiMessage):
    def new_del_msg(self, del_msg: gnmi_pb2.Path) -> Self:
        msg = copy.deepcopy(self)
        msg.scan_gnmi_path(del_msg)
        return msg


@dataclass
class _BasePluginConfig:
    """Device base Class configuration"""
    instance_name: str = 'default'
    dev_name: str = 'default'
    metric_prefix: str = 'gnmi'


class BasePlugin(ABC):
    """gNMI messages parser base Class"""
    def __init__(self, global_cfg: dict[str, str], device_cfg: dict[str, Any], exporter: PromClient):
        self.config = _BasePluginConfig()
        self._parse_config(global_cfg, device_cfg)

        # Object logic
        self._buffer: list[gnmi_pb2.Notification] = []  # type: ignore
        self._mutex = threading.Lock()
        self._on_sync: bool = False

        # Object data
        self.update_list: list[GnmiUpdate] = []
        self.delete_list: list[GnmiDelete] = []

        # Self-registering to the exporter
        exporter.register_plugin(self)

    @abstractmethod
    def get_paths(self) -> common_types.GnmiPaths:
        pass

    @abstractmethod
    async def fetch_metric_bundles(self) -> list[common_types.GnmiMetricBundle]:
        # Implementers should call checkout() before process update and delete lists
        pass

    def gnmi_notification_handler(self, msg: gnmi_pb2.Notification) -> None:
        with self._mutex:
            self._buffer.append(msg)

    def set_sync_status(self, on_sync: bool) -> None:
        with self._mutex:
            # Clear buffer on True->False transition
            if not on_sync and self._on_sync:
                self._buffer.clear()

            self._on_sync = on_sync

    def checkout(self):
        # Clear output lists
        self.update_list.clear()
        self.delete_list.clear()

        # Get the notification buffer content
        nf_buf: list[gnmi_pb2.Notification] = []  # type: ignore
        with self._mutex:
            if self._on_sync:
                nf_buf = copy.deepcopy(self._buffer)
                self._buffer.clear()

        # Unpack notifications into Update and Delete objects
        for nf in nf_buf:
            g_update = GnmiUpdate(nf)
            g_delete = GnmiDelete(nf)

            for nf_upd in nf.update:
                self.update_list.append(g_update.new_upd_msg(nf_upd))

            for nf_del in nf.delete:
                self.delete_list.append(g_delete.new_del_msg(nf_del))

        # Sort messages by timestamp
        self.update_list.sort(key=lambda msg: msg.timestamp)
        self.delete_list.sort(key=lambda msg: msg.timestamp)

    def _parse_config(self, global_cfg: dict[str, str], device_cfg: dict[str, Any]) -> None:
        """ Parse and load config """
        # From global configuration
        if 'instance_name' in global_cfg:
            self.config.instance_name = global_cfg['instance_name']
        if 'metric_prefix' in global_cfg:
            self.config.metric_prefix = global_cfg['metric_prefix']

        # From device configuration
        if 'name' in device_cfg:
            self.config.dev_name = device_cfg['name']
