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
import re
import time
import logging
from typing import Any
from collections.abc import Iterable

# Local modules
import src.common_types as common_types
import src.plugins.base_plug as base_plug
from src.exporter.promexp import PromClient

# Constants
_DATA_MODEL = 'openconfig-interfaces'
_PATHS_TO_SUBSCRIBE = common_types.GnmiPaths(xpath_list=['/interfaces/interface/state',
                                             '/interfaces/interface/subinterfaces/subinterface/state'],
                                             datamodels=[_DATA_MODEL],
                                             origin='openconfig',
                                             target='oc_interfaces')
# Labels and metrics names (derived from data-model)
_PLUGIN_LABEL_SET = ['instance-name', 'data-model', 'device']
_SUBIFACE_LABEL_SET = ['name', 'index', 'mtu', 'description', 'ifindex', 'admin-status', 'oper-status']
_SUBIFACE_METRIC_SET = ['in-octets', 'in-pkts', 'in-unicast-pkts', 'in-broadcast-pkts',
                        'in-multicast-pkts', 'in-errors', 'in-discards', 'out-octets', 'out-pkts',
                        'out-unicast-pkts', 'out-broadcast-pkts', 'out-multicast-pkts', 'out-discards',
                        'out-errors', 'last-clear', 'last-change', 'in-unknown-protos', 'in-fcs-errors',
                        'carrier-transitions']
_IFACE_LABEL_SET = ['name', 'mtu', 'description', 'ifindex', 'admin-status', 'oper-status']
_IFACE_METRIC_SET = _SUBIFACE_METRIC_SET + ['resets']


# Data model keys indexes
_IFACE_PATH_NAME = 1, 'name'
_SUBIFACE_PATH_INDEX = 3, 'index'


class IfaceTable:
    def __init__(self):
        # outer key: interface full name (e.g.: 'eth1' or 'eth1.100')
        # inner key: entry name (e.g.: 'in-octets' or 'oper-status')
        self.table: dict[str, dict[str, Any]] = {}  # type: ignore

    def add_table_entry(self, if_full_name: str, label_tpl: list[str], metric_tpl: list[str]) -> None:
        """Adds the metrics skeleton of an interface to the table, following the template structure"""
        try:
            if if_full_name in self.table:
                return
            # Create interface entry
            self.table.update({if_full_name: {'name': ''}})

            # Fill up the newly created record with the template
            for label in label_tpl:
                self.table[if_full_name][label] = ''
            for metric in metric_tpl:
                self.table[if_full_name][metric] = 0

        except KeyError:
            logging.debug(f"Interface table entry {if_full_name} creation failed.")

    def update_table_entry(self, if_full_name: str, entry_name: str, entry_val: Any) -> None:
        try:
            self.table[if_full_name][entry_name] = entry_val
        except KeyError:
            logging.debug(f"Interface {if_full_name} table entry {entry_name} update failed.")

    def items(self) -> Iterable[str]:
        for iface in self.table:
            yield iface

    def get_entry(self, if_name: str, entry_name: str) -> Any:
        try:
            return self.table[if_name][entry_name]
        except KeyError:
            logging.debug(f"oc-interfaces: {entry_name} not found in {if_name}.")
            return ' '

    def clear(self):
        self.table.clear()


class MetricTable:
    def __init__(self):
        # outer key: metric name
        self.table: dict[str, list[common_types.GnmiMetric]] = {}  # type: ignore

    def add_metric(self, name: str, metric: common_types.GnmiMetric) -> None:
        if name not in self.table:
            self.table[name] = []
        self.table[name].append(metric)

    def get_metrics(self, name: str) -> Iterable[common_types.GnmiMetric]:
        try:
            for metric in self.table[name]:
                yield metric
        except KeyError:
            logging.debug(f"oc-interfaces: Metric {name} not found in table.")
            yield common_types.GnmiMetric()

    def clear(self) -> None:
        self.table.clear()


class OcInterfaces(base_plug.BasePlugin):
    def __init__(self, global_cfg: dict[str, str], device_cfg: dict[str, Any], exporter: PromClient):
        super().__init__(global_cfg, device_cfg, exporter)

        # Interface tables
        self.iface_table = IfaceTable()
        self.subiface_table = IfaceTable()

        # Metric tables
        self.iface_metrics_table = MetricTable()
        self.subiface_metrics_table = MetricTable()

        # Metric bundles list (module output)
        self.bundle_list: list[common_types.GnmiMetricBundle] = []

    def get_paths(self) -> common_types.GnmiPaths:
        """Returns paths to be subscribed"""
        return _PATHS_TO_SUBSCRIBE

    async def fetch_metric_bundles(self) -> list[common_types.GnmiMetricBundle]:
        """From client's gNMI messages to a list of GnmiMetricBundle ready to export
        -- This is called by Prometheus when a scrape event occurs --
        """
        # Clear tables
        self.clear_all_tables()

        # Get gNMI messages from buffer
        self.checkout()
        if not self.update_list:
            return []

        # Creates empty interface and subinterface table
        self.build_tables()

        # Loads interface and subinterface table with device gNMI data
        self.update_tables()

        # Build the metrics table (from interface tables)
        self.build_metrics()

        # Last step: build the bundle list (from metrics table)
        self.build_bundle_list()

        return self.bundle_list

    def clear_all_tables(self) -> None:
        """Wipes object data structures before being populated."""
        self.iface_table.clear()
        self.subiface_table.clear()
        self.iface_metrics_table.clear()
        self.subiface_metrics_table.clear()
        self.bundle_list.clear()

    def build_tables(self) -> None:
        """Scans the message buffer for interface names and builds the table skeleton
        -- Call after checkout() --
        """
        for update in self.update_list:
            path_str = ''.join(update.path)

            # Interfaces
            # TODO: Maybe it is safer to match the container instead the leaf "name". Less efficient but safer.
            if re.fullmatch('interfacesinterfacestatename', path_str):
                fullname = update.get_path_key(*_IFACE_PATH_NAME)
                self.iface_table.add_table_entry(if_full_name=fullname,
                                                 label_tpl=_IFACE_LABEL_SET,
                                                 metric_tpl=_IFACE_METRIC_SET)

            # Subinterfaces
            if re.fullmatch('interfacesinterfacesubinterfacessubinterfacestatename', path_str):
                fullname = ''.join([update.get_path_key(*_IFACE_PATH_NAME), '.',
                                    update.get_path_key(*_SUBIFACE_PATH_INDEX)])
                self.subiface_table.add_table_entry(if_full_name=fullname,
                                                    label_tpl=_SUBIFACE_LABEL_SET,
                                                    metric_tpl=_SUBIFACE_METRIC_SET)

    def update_tables(self) -> None:
        """Scans the message buffer and populate interface table lists whit gNMI data.
         -- Call after build_tables() --
        """
        for update in self.update_list:
            path_str = ''.join(update.path)

            # Interfaces
            if re.match('interfacesinterfacestate', path_str):
                if update.path[-1] in _IFACE_LABEL_SET:
                    value = str(update.val)
                elif update.path[-1] in _IFACE_METRIC_SET:
                    value = int(update.val)  # type: ignore
                else:
                    continue
                self.iface_table.update_table_entry(if_full_name=update.get_path_key(*_IFACE_PATH_NAME),
                                                    entry_name=update.path[-1],
                                                    entry_val=value)

            # Subinterfaces
            if re.match('interfacesinterfacesubinterfacessubinterfacestate', path_str):
                if update.path[-1] in _SUBIFACE_LABEL_SET:
                    value = str(update.val)
                    if update.path[-1] == 'name':
                        value = update.get_path_key(*_IFACE_PATH_NAME)
                elif update.path[-1] in _SUBIFACE_METRIC_SET:
                    value = int(update.val)  # type: ignore
                else:
                    continue
                ifl = ''.join([update.get_path_key(*_IFACE_PATH_NAME), '.', update.get_path_key(*_SUBIFACE_PATH_INDEX)])
                self.subiface_table.update_table_entry(if_full_name=ifl,
                                                       entry_name=update.path[-1],
                                                       entry_val=value)

    def build_metrics(self) -> None:
        """Scans interface tables and builds the metrics table
        -- Call after update_tables() --
        """
        # Interfaces
        for metric in _IFACE_METRIC_SET:
            for iface in self.iface_table.items():
                gnmi_metric = common_types.GnmiMetric(labelval=[self.config.instance_name,
                                                                _DATA_MODEL,
                                                                self.config.dev_name])
                # build metric label values
                for label in _IFACE_LABEL_SET:
                    gnmi_metric.labelval.append(self.iface_table.get_entry(if_name=iface, entry_name=label))

                # get value and timestamp
                gnmi_metric.val = self.iface_table.get_entry(if_name=iface, entry_name=metric)
                # TODO: Timestamp was lost somewhere and not available here... Fix it!
                gnmi_metric.ts = time.time()
                self.iface_metrics_table.add_metric(name=metric, metric=gnmi_metric)

        # Subinterfaces
        for metric in _SUBIFACE_METRIC_SET:
            for iface in self.subiface_table.items():
                gnmi_metric = common_types.GnmiMetric(labelval=[self.config.instance_name,
                                                                _DATA_MODEL,
                                                                self.config.dev_name])
                # build metric label values
                for label in _SUBIFACE_LABEL_SET:
                    gnmi_metric.labelval.append(self.subiface_table.get_entry(if_name=iface, entry_name=label))

                # get value and timestamp
                gnmi_metric.val = self.subiface_table.get_entry(if_name=iface, entry_name=metric)
                # TODO: Timestamp was lost somewhere and not available here... Fix it!
                gnmi_metric.ts = time.time()
                self.subiface_metrics_table.add_metric(name=metric, metric=gnmi_metric)

    def build_bundle_list(self) -> None:
        """Scans metric tables and build the metric bundle list
        -- Call alter build_metrics() --
        """
        # Interfaces
        label_set = []
        for label in _PLUGIN_LABEL_SET + _IFACE_LABEL_SET:
            label_set.append(label.replace('-', '_'))
        for name in _IFACE_METRIC_SET:
            metric_name = ''.join([self.config.metric_prefix, '_iface_', name.replace('-', '_')])
            bundle = common_types.GnmiMetricBundle(type=common_types.GnmiMetricType.COUNTER,
                                                   device_name=self.config.dev_name,
                                                   metric_name=metric_name,
                                                   labelset=label_set)
            for metric in self.iface_metrics_table.get_metrics(name=name):
                bundle.metrics.append(metric)

            self.bundle_list.append(bundle)

        # Subinterfaces
        label_set = []
        for label in _PLUGIN_LABEL_SET + _SUBIFACE_LABEL_SET:
            label_set.append(label.replace('-', '_'))
        for name in _SUBIFACE_METRIC_SET:
            metric_name = ''.join([self.config.metric_prefix, '_subiface_', name.replace('-', '_')])
            bundle = common_types.GnmiMetricBundle(type=common_types.GnmiMetricType.COUNTER,
                                                   device_name=self.config.dev_name,
                                                   metric_name=metric_name,
                                                   labelset=label_set)
            for metric in self.subiface_metrics_table.get_metrics(name=name):
                bundle.metrics.append(metric)

            self.bundle_list.append(bundle)
