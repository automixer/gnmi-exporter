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
import grpc  # type: ignore
import logging
import threading
import dataclasses
from enum import Enum
from typing import Any
from collections.abc import Callable, Iterable

# Local Modules
import src.common_types as ct
import src.gnmi_client.utils as utils
import src.gnmi_pb2.gnmi_pb2 as gnmi_pb2
import src.gnmi_pb2.gnmi_pb2_grpc as gnmi_pb2_grpc
from src.exporter.promexp import PromClient

# Plugins
from src.plugins.base_plug import BasePlugin
from src.plugins.oc_interfaces.oc_if import OcInterfaces


@dataclasses.dataclass(frozen=True)
class _DataModel:
    """yang DataModel infos"""
    name: str
    organization: str
    version: str


class _EncodingTypes(Enum):
    """gNMI encoding types"""
    JSON = 0
    BYTES = 1
    PROTO = 2
    ASCII = 3
    JSON_IETF = 4


class DialError(utils.Error):
    """Error connecting to the device"""


# Constants
_RPC_TIMEOUT = 10
_RECONNECT_TIMEOUT = 10
_PREFERRED_ENCODINGS = (_EncodingTypes.PROTO, _EncodingTypes.JSON, _EncodingTypes.JSON_IETF, _EncodingTypes.ASCII)


@dataclasses.dataclass
class _GnmiClientConfig:
    """GnmiClient configuration keys """
    scrape_interval: int = 60
    oversampling: int = 2
    wd_multiplier: int = 3
    force_encoding: str = ''
    bypass_msg_routing: bool = False
    dev_name: str = 'default'
    ip: str = ''
    port: int = 0
    user: str = ''
    password: str = ''
    plugins: set[str] = dataclasses.field(default_factory=set)


class GnmiClient(threading.Thread):
    def __init__(self, global_cfg: dict[str, str], device_cfg: dict[str, Any], exporter: PromClient):
        # Config
        self._config = _GnmiClientConfig()
        self._parse_config(global_cfg, device_cfg)

        # Client logic
        super().__init__(name=self._config.dev_name)
        self._mutex = threading.Lock()
        self._exit: bool = False
        self._event = threading.Event()
        self._plugin_table: dict[str, BasePlugin] = {}
        self._path_list: list[ct.GnmiPaths] = []

        # Target related
        self._target = f"{self._config.ip}:{self._config.port}"
        self._creds = [('username', self._config.user), ('password', self._config.password)]
        self._selected_encoding = _EncodingTypes.JSON

        # Load plugins
        for plugin in self._config.plugins:
            if plugin == 'oc_interfaces':
                self._plugin_table[plugin] = OcInterfaces(global_cfg, device_cfg, exporter)
                self._path_list.append(self._plugin_table[plugin].get_paths())

    def run(self):
        sub_response = [gnmi_pb2.SubscribeResponse()]
        while True:
            channel = self._create_channel()
            stub = gnmi_pb2_grpc.gNMIStub(channel)
            wd = _WatchDog(timeout=self._config.scrape_interval * self._config.wd_multiplier,
                           expired_handler=self._event.set)

            # Connect and subscribe plugins gnmi paths
            while True:
                try:
                    logging.info(f"Connecting to {self._config.dev_name} ...")
                    self._check_caps(stub)  # Check if the device supports required features
                    sub_response = self._subscribe(stub)  # Subscribe
                except DialError as dial_err:
                    logging.error(dial_err)
                    self._event.wait(timeout=_RECONNECT_TIMEOUT)
                    # Time to exit?
                    with self._mutex:
                        if self._exit:
                            channel.close()
                            return
                        else:
                            continue
                # Done!
                logging.info(f"{self._config.dev_name} is now online ...")
                break

            # Setup gNMI notifications collector
            gnmi_collector = _GnmiCollector(sub_response=sub_response,
                                            gnmi_sr_callback=self.route_gnmi_sr,
                                            keepalive=wd.kick,
                                            dev_name=self._config.dev_name)
            # Start watchdog and collector
            wd.start()
            gnmi_collector.start()

            # Put this thread in wait state while receiving telemetries
            self._event.wait()

            # Cleaning up...
            for plugin in self._plugin_table.values():
                plugin.set_sync_status(False)
            self._event.clear()
            wd.stop()
            channel.close()
            gnmi_collector.wait_for_stop()

            # Time to exit?
            with self._mutex:
                if self._exit:
                    break

    def close(self) -> None:
        with self._mutex:
            self._exit = True
        self._event.set()
        self.join()

    def _create_channel(self) -> grpc.Channel:
        # TODO: Handle ssl channels
        return grpc.insecure_channel(target=self._target, options=self._creds)

    def route_gnmi_sr(self, sr: gnmi_pb2.SubscribeResponse) -> None:
        # Note: gNMI Extensions are not implemented (yet)
        if sr.HasField('update'):  # This is a gNMI Notification
            if self._config.bypass_msg_routing:
                # Broadcast msg to all plugins
                for plugin in self._plugin_table.values():
                    plugin.gnmi_notification_handler(sr.update)
            elif sr.update.prefix.target in self._plugin_table.keys():
                # Normal message routing
                self._plugin_table[sr.update.prefix.target].gnmi_notification_handler(sr.update)
            else:
                # This should not happen. It means that the device does not support path targets
                # https://github.com/openconfig/reference/blob/master/rpc/gnmi/gnmi-specification.md#2221-path-target
                logging.error(f"{self._config.dev_name} does not support path target. Enable <bypass_msg_routing>.")

        elif sr.HasField('sync_response'):  # This is a sync_response message (bool)
            # sync_response must be broadcasted to all plugins
            for plugin in self._plugin_table.values():
                plugin.set_sync_status(sr.sync_response)

    def _check_caps(self, stub: gnmi_pb2_grpc.gNMIStub) -> None:
        req = gnmi_pb2.CapabilityRequest()

        # ======= Get device capabilities =======
        try:
            resp: gnmi_pb2.CapabilityResponse = stub.Capabilities(req, metadata=self._creds, timeout=_RPC_TIMEOUT)
        except grpc.RpcError as rpc_error:
            raise DialError(f"gNMI capabilities() call to {self._config.dev_name} failed with: {rpc_error.code()}")

        # ======= Check datamodels support =======
        supported_models: dict[str, _DataModel] = {}
        for model_name in resp.supported_models:
            supported_models[model_name.name] = _DataModel(name=model_name.name,
                                                           organization=model_name.organization,
                                                           version=model_name.version)
        for plugin in self._path_list:
            for model in plugin.datamodels:
                if model not in supported_models:
                    raise DialError(f"{self._config.dev_name} does not support {model} yang data model.")

        # ======= Pick an encoding =======
        if self._config.force_encoding:
            for enc in _PREFERRED_ENCODINGS:
                if enc.name == self._config.force_encoding:
                    self._selected_encoding = enc
        else:
            for enc in _PREFERRED_ENCODINGS:
                if enc.value in resp.supported_encodings:
                    self._selected_encoding = enc
                    break

    def _subscribe(self, stub: gnmi_pb2_grpc.gNMIStub) -> gnmi_pb2.SubscribeResponse:
        req_list = []
        sample_interval = int(self._config.scrape_interval * pow(10, 9) / self._config.oversampling)

        for plugin_path in self._path_list:
            # Create SubscriptionList
            sublist = gnmi_pb2.SubscriptionList(prefix=gnmi_pb2.Path(target=plugin_path.target),
                                                mode='STREAM',
                                                subscription=[],
                                                qos=None,
                                                allow_aggregation=False,
                                                encoding=self._selected_encoding.name,
                                                updates_only=False)

            # Create Subscriptions and add them to SubScriptionList
            sample_interval = int(self._config.scrape_interval * pow(10, 9) / self._config.oversampling)
            for xpath in plugin_path.xpath_list:
                try:
                    path = utils.xpath_to_gnmi(xpath=xpath, origin=plugin_path.origin)
                except utils.XpathError as e:
                    logging.error(f"The xpath {xpath} is malformed.")
                    continue

                sublist.subscription.append(gnmi_pb2.Subscription(path=path,
                                                                  mode='SAMPLE',
                                                                  sample_interval=sample_interval,
                                                                  suppress_redundant=False))
            # Create request and add to the request list
            req_list.append(gnmi_pb2.SubscribeRequest(subscribe=sublist))

        # Subscribe to target
        def generate(rlist: list[gnmi_pb2.SubscribeRequest]) -> Iterable[gnmi_pb2.SubscribeRequest]:
            for req in rlist:
                yield req
        try:
            return stub.Subscribe(generate(req_list), metadata=self._creds)
        except grpc.RpcError as rpc_error:
            raise DialError(f"gNMI stream from {self._target} failed with: {rpc_error.code()}"
                            f" - details: {rpc_error.details()}")

    def _parse_config(self, global_cfg: dict[str, str], device_cfg: dict[str, Any]) -> None:
        """Parse and load config data"""
        # From global configuration
        if 'scrape_interval' in global_cfg:
            self._config.scrape_interval = int(global_cfg['scrape_interval'])
        if 'oversampling' in global_cfg:
            self._config.oversampling = int(global_cfg['oversampling'])
        if 'wd_multiplier' in global_cfg:
            self._config.wd_multiplier = int(global_cfg['wd_multiplier'])

        # From device configuration
        if 'force_encoding' in device_cfg:
            self._config.force_encoding = device_cfg['force_encoding']
        if 'bypass_msg_routing' in device_cfg:
            self._config.bypass_msg_routing = bool(device_cfg['bypass_msg_routing'])
        if 'name' in device_cfg and device_cfg['name']:
            self._config.dev_name = device_cfg['name']
        if 'ip' in device_cfg and device_cfg['ip']:
            self._config.ip = device_cfg['ip']
        if 'port' in device_cfg:
            self._config.port = int(device_cfg['port'])
        if 'user' in device_cfg:
            self._config.user = device_cfg['user']
        if 'password' in device_cfg:
            self._config.password = device_cfg['password']
        if 'plugins' in device_cfg and isinstance(device_cfg['plugins'], set):
            self._config.plugins = device_cfg['plugins'].copy()


class _GnmiCollector(threading.Thread):
    def __init__(self, sub_response: gnmi_pb2.SubscribeResponse,
                 gnmi_sr_callback: Callable[[gnmi_pb2.SubscribeResponse], None],
                 keepalive: Callable,
                 dev_name: str):
        super().__init__()
        self._gnmi_stream = sub_response
        self._callback = gnmi_sr_callback
        self._keepalive = keepalive
        self._dev_name = dev_name

    def run(self):
        try:
            self._gnmi_stream: Iterable[gnmi_pb2.SubscribeResponse]  # type: ignore
            for sr in self._gnmi_stream:
                self._keepalive()
                self._callback(sr)
        except grpc.RpcError as rpc_error:
            logging.info(f"gNMI stream from {self._dev_name} exited with: {rpc_error.code()}")
            return

    def wait_for_stop(self) -> None:
        self.join()


class _WatchDog(threading.Thread):
    def __init__(self, timeout: int, expired_handler: Callable):
        super().__init__()
        self._fire = expired_handler
        self._timeout: int = timeout
        self._counter: int = timeout
        self._exit: bool = False
        self._event = threading.Event()
        self._mutex = threading.Lock()

    def run(self) -> None:
        while self._counter:
            self._event.wait(1)
            with self._mutex:
                if self._exit:
                    return
                self._counter -= 1
            self._event.clear()

        # Expired
        self._fire()
        return

    def kick(self) -> None:
        with self._mutex:
            self._counter = self._timeout

    def stop(self) -> None:
        with self._mutex:
            self._exit = True
        self._event.set()
        self.join()
