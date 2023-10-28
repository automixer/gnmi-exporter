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
import time
import asyncio
from threading import Lock
from typing import Protocol
from dataclasses import dataclass
from prometheus_client import start_http_server
from prometheus_client.registry import Collector
from prometheus_client.core import GaugeMetricFamily, CounterMetricFamily, REGISTRY

# Local Modules
from src.common_types import GnmiMetricBundle, GnmiMetricType, GnmiMetric


# Interfaces
class Plugin(Protocol):
    async def fetch_metric_bundles(self) -> list[GnmiMetricBundle]:
        ...


@dataclass
class _PromClientConfig:
    """PromClient config"""
    instance_name: str = 'default'
    listen_address: str = '0.0.0.0'
    listen_port: int = 9456
    metric_prefix: str = 'gnmi'


class PromClient(Collector):
    """ gNMI Exporter Prometheus client"""
    def __init__(self, global_cfg: dict):
        self._config = _PromClientConfig()
        self._parse_config(global_cfg)

        self._plugin_list: list[Plugin] = []
        self._mutex = Lock()
        self._bundle_table: dict[str, GnmiMetricBundle] = {}
        self._collected_devices: int = 0
        self._collected_plugins: int = 0

    def register_plugin(self, plug: Plugin) -> None:
        """ Register Plugin() instances here"""
        with self._mutex:
            self._plugin_list.append(plug)

    def unregister_all(self) -> None:
        """ Release all the registered devices """
        with self._mutex:
            self._plugin_list.clear()

    def start(self) -> None:
        """ Register and start Prometheus http server """
        REGISTRY.register(self)
        start_http_server(self._config.listen_port, self._config.listen_address)

    def collect(self):
        """ Scrape event occurred """
        # Gather data
        asyncio.run(self._query_plugins())

        # Compute self-statistics
        self._compute_stats()

        # Export the metrics bundle table
        for bundle in self._bundle_table.values():
            if bundle.type == GnmiMetricType.UNKNOWN:
                continue

            # Exporting Counter metrics types
            elif bundle.type == GnmiMetricType.COUNTER:
                c = CounterMetricFamily(name=bundle.metric_name,
                                        documentation=bundle.documentation,
                                        labels=bundle.labelset)
                for metric in bundle.metrics:
                    c.add_metric(labels=metric.labelval,
                                 value=float(metric.val),
                                 timestamp=metric.ts)
                yield c

            # Exporting Gauge metrics types
            elif bundle.type == GnmiMetricType.GAUGE:
                g = GaugeMetricFamily(name=bundle.metric_name,
                                      documentation=bundle.documentation,
                                      labels=bundle.labelset)
                for metric in bundle.metrics:
                    g.add_metric(labels=metric.labelval,
                                 value=metric.val,
                                 timestamp=metric.ts)
                yield g

        # Clear (and release memory) bundle table
        self._bundle_table.clear()

    async def _query_plugins(self) -> None:
        """ Gather metrics from plugins (concurrently) """
        self._collected_plugins = 0
        self._collected_devices = 0

        if self._plugin_list:
            # Query plugins
            self._mutex.acquire()
            coro_list = []
            for plugin in self._plugin_list:
                coro_list.append(plugin.fetch_metric_bundles())

            response = await asyncio.gather(*coro_list)
            self._mutex.release()

            # Walk the plugin list (response)
            for plugin in response:
                # Check if the plugin returned something
                if (isinstance(plugin, list)
                   and plugin
                   and isinstance(plugin[0], GnmiMetricBundle)
                   and plugin[0].is_valid()):
                    # Update collected plugins gauge
                    self._collected_plugins += 1
                else:
                    continue

                # Add plugin returned data to bundle table
                device_names = set()
                for bundle in plugin:
                    if isinstance(bundle, GnmiMetricBundle) and bundle.is_valid():
                        device_names.add(bundle.device_name)
                        if bundle.metric_name not in self._bundle_table:
                            self._bundle_table[bundle.metric_name] = bundle
                        else:
                            self._bundle_table[bundle.metric_name].metrics.extend(bundle.metrics)

                # Update collected devices gauge
                self._collected_devices = len(device_names)

    def _compute_stats(self) -> None:
        """ gNMI Exporter self diagnostic metrics """
        # Configured devices
        g = GnmiMetricBundle(type=GnmiMetricType.GAUGE,
                             metric_name=self._config.metric_prefix + '_configured_devices',
                             documentation='Number of configured devices',
                             labelset=['instance_name'],
                             metrics=[GnmiMetric(labelval=[self._config.instance_name],
                                                 val=len(self._plugin_list),
                                                 ts=time.time())])
        self._bundle_table['configured_devices'] = g

        # Collect devices
        g = GnmiMetricBundle(type=GnmiMetricType.GAUGE,
                             metric_name=self._config.metric_prefix + '_collected_devices',
                             documentation='Number of actively monitored devices',
                             labelset=['instance_name'],
                             metrics=[GnmiMetric(labelval=[self._config.instance_name],
                                                 val=self._collected_devices,
                                                 ts=time.time())])
        self._bundle_table['collected_devices'] = g

        # Collected plugins
        g = GnmiMetricBundle(type=GnmiMetricType.GAUGE,
                             metric_name=self._config.metric_prefix + '_collected_plugins',
                             documentation='Number of actively monitored plugin instances',
                             labelset=['instance_name'],
                             metrics=[GnmiMetric(labelval=[self._config.instance_name],
                                                 val=self._collected_plugins,
                                                 ts=time.time())])
        self._bundle_table['collected_plugins'] = g

        # Collected metrics
        g = GnmiMetricBundle(type=GnmiMetricType.GAUGE,
                             metric_name=self._config.metric_prefix + '_collected_metrics',
                             documentation='Number of collected metrics',
                             labelset=['instance_name'],
                             metrics=[GnmiMetric(labelval=[self._config.instance_name],
                                                 val=len(self._bundle_table) + 1,  # +1 is this metric
                                                 ts=time.time())])
        self._bundle_table['collected_metrics'] = g

        # Collected series
        collected_series = 0
        for metric in self._bundle_table.values():
            collected_series += len(metric.metrics)

        g = GnmiMetricBundle(type=GnmiMetricType.GAUGE,
                             metric_name=self._config.metric_prefix + '_collected_series',
                             documentation='Number of collected series',
                             labelset=['instance_name'],
                             metrics=[GnmiMetric(labelval=[self._config.instance_name],
                                                 val=collected_series,
                                                 ts=time.time())])
        self._bundle_table['collected_series'] = g

    def _parse_config(self, global_cfg: dict) -> None:
        """ Parse and load user config """
        if 'instance_name' in global_cfg:
            self._config.instance_name = global_cfg['instance_name']
        if 'listen_address' in global_cfg:
            self._config.listen_address = global_cfg['listen_address']
        if 'listen_port' in global_cfg:
            self._config.listen_port = int(global_cfg['listen_port'])
        if 'metric_prefix' in global_cfg:
            self._config.metric_prefix = global_cfg['metric_prefix']
