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

import dataclasses
from enum import Enum


@dataclasses.dataclass
class GnmiPaths:
    """gNMI paths and datamodels to be subscribed"""
    xpath_list: list[str]
    datamodels: list[str]
    origin: str
    target: str


@dataclasses.dataclass
class GnmiMetric:
    """Single metric data"""
    labelval: list[str] = dataclasses.field(default_factory=list)
    val: int = 0
    ts: int = 0


class GnmiMetricType(Enum):
    """supported metric types"""
    UNKNOWN = 0
    COUNTER = 1
    GAUGE = 2


@dataclasses.dataclass
class GnmiMetricBundle:
    """A bundle of GnmiMetric(s) with related metadata"""
    type: GnmiMetricType = GnmiMetricType.UNKNOWN
    device_name: str = ''
    metric_name: str = ''
    documentation: str = ''
    labelset: list[str] = dataclasses.field(default_factory=list)
    metrics: list[GnmiMetric] = dataclasses.field(default_factory=list)

    def is_valid(self) -> bool:
        if (not isinstance(self.metric_name, str)
                or not self.device_name
                or not self.metric_name
                or self.type == GnmiMetricType.UNKNOWN):
            return False
        for mt in self.metrics:
            if len(mt.labelval) != len(self.labelset):
                return False
        return True
