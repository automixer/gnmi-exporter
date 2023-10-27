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
import logging
import signal
from typing import Any


# Local Modules
from src.exporter.promexp import PromClient
from src.gnmi_client.client import GnmiClient


class App:
    """ Application main module """
    def __init__(self, raw_cfg: dict):
        self._global_cfg: dict[str, str] = {}
        self._devices_cfg: list[dict[str, Any]] = []
        self._parse_raw_cfg(raw_cfg)

        self._prom_client: PromClient
        self._devices_list: list[GnmiClient] = []

    def start(self) -> None:
        # Registering os signals
        signal.signal(signal.SIGINT, self.close)
        signal.signal(signal.SIGTERM, self.close)

        # Load Prometheus client
        logging.info(f"Initializing Prometheus exporter module...")
        self._prom_client = PromClient(self._global_cfg)
        self._prom_client.start()

        # Load gNMI clients
        plug_cnt = 0
        for dev_cfg in self._devices_cfg:
            new_dev = GnmiClient(self._global_cfg, dev_cfg, self._prom_client)
            new_dev.start()
            self._devices_list.append(new_dev)
            for _ in dev_cfg['plugins']:
                plug_cnt += 1
        logging.info(f"{len(self._devices_list)} device(s) and {plug_cnt} plugin(s) successfully loaded...")

        # Wait for termination signal
        signal.pause()
        return

    def close(self, signum, frame) -> None:
        self._prom_client.unregister_all()
        for device in self._devices_list:
            device.close()

    def _parse_raw_cfg(self, raw_cfg: dict) -> None:
        # Global config
        for key, value in raw_cfg['global'].items():
            self._global_cfg[key] = str(value)

        # Device template
        plugins_tpl: set[str] = set()
        devices_tpl: dict[str, Any] = {}
        if 'device_template' in raw_cfg:
            if 'plugins' in raw_cfg['device_template'] and isinstance(raw_cfg['device_template']['plugins'], list):
                plugins_tpl = set(raw_cfg['device_template'].pop('plugins'))
            for key, value in raw_cfg['device_template'].items():
                devices_tpl[key] = str(value)

        # Devices
        for device in raw_cfg['devices']:
            new_dev = devices_tpl.copy()
            if 'plugins' in device:
                new_dev['plugins'] = set(device.pop('plugins'))
            else:
                new_dev['plugins'] = plugins_tpl.copy()
            for key, value in device.items():
                new_dev[key] = str(value)
            self._devices_cfg.append(new_dev)

        return
