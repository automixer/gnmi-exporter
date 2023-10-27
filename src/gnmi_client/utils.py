"""Copyright 2018 Google LLC

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    https://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

The body of these helper functions was taken from:
https://raw.githubusercontent.com/google/gnxi/master/gnmi_cli_py/py_gnmicli.py  __version__ = '0.4'
2023 Luca Cilloni
"""

# Modules
import re
from typing import Optional

# Local modules
from src.gnmi_pb2.gnmi_pb2 import Path, PathElem

# Constants
_RE_PATH_COMPONENT = re.compile(r'''
^
(?P<pname>[^[]+)  # gNMI path name
(\[(?P<key>\w\D+)   # gNMI path key
=
(?P<value>.*)    # gNMI path value
\])?$
''', re.VERBOSE)


class Error(Exception):
    """Module-level Exception class."""


class XpathError(Error):
    """Error parsing xpath provided."""


def xpath_to_gnmi(xpath: str, origin: str = 'openconfig', target: Optional[str] = None) -> Path:
    """Parses the xpath names and returns a gNMI Path Class object.

    Takes an input string and converts it to a gNMI Path Class object
    with the desired origin.

    Args:
      xpath: (str) xpath formatted path.
      origin: (str) gNMI Path origin. default to openconfig
      target: Optional (str) Associate path with subscriber

    Returns:
      a gnmi_pb2.Path object representing the supplied xpath and origin.

    Raises:
    XpathError: Unable to parse the xpath provided.
    """
    if not xpath or xpath == '/':
        raise XpathError('a blank xpath was provided.')
    p_names = re.split(r'''/(?=(?:[^\[\]]|\[[^\[\]]+\])*$)''',
                       xpath.strip('/').strip('/'))  # Removes leading/trailing '/'.
    gnmi_elems = []
    for word in p_names:
        word_search = _RE_PATH_COMPONENT.search(word)
        if not word_search:  # Invalid path specified.
            raise XpathError('xpath component parse error: %s' % word)
        if word_search.group('key') is not None:  # A path key was provided.
            tmp_key = {}
            for x in re.findall(r'\[([^]]*)\]', word):
                tmp_key[x.split("=")[0]] = x.split("=")[-1]
                gnmi_elems.append(PathElem(name=word_search.group(
                  'pname'), key=tmp_key))
        else:
            gnmi_elems.append(PathElem(name=word, key={}))
    return Path(elem=gnmi_elems, origin=origin, target=target)
