#!/usr/bin/env python3
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
import argparse
import logging
import yaml

# Local Modules
import src.core as core


# Functions
def get_cmd_line():
    """ Gather command line parameters """
    argparser = argparse.ArgumentParser()
    argparser.add_argument('--config', default='config.yaml', help='Config file')
    argparser.add_argument('--dbg', action='store_true', help='Debug logging')
    args = argparser.parse_args()
    return args


def load_config_from_file(cfg_file: str) -> dict:
    """ Load app configuration """
    try:
        with (open(cfg_file, 'r') as f):
            raw_cfg = yaml.safe_load(f)

            if not raw_cfg and not isinstance(raw_cfg, dict):
                logging.error('Invalid configuration file. Quitting application...')
                return {}

            if 'global' not in raw_cfg or not isinstance(raw_cfg['global'], dict):
                logging.error('Missing <global> section. Quitting application...')
                return {}

            if 'device_template' not in raw_cfg or not isinstance(raw_cfg['device_template'], dict):
                logging.warning('Missing <device_template> section. All ok?')

            if 'devices' not in raw_cfg or not isinstance(raw_cfg['devices'], list) or not raw_cfg['devices']:
                logging.error('The devices is empty or invalid. Quitting application...')
                return {}

            return raw_cfg

    except FileNotFoundError:
        logging.error(f"Configuration file {cfg_file} not found. Quitting application...")
        return {}


def main() -> int:
    args = get_cmd_line()

    # Setup logging
    lvl = logging.INFO
    if args.dbg:
        lvl = logging.DEBUG
    logging.basicConfig(format='%(asctime)s [gnmi-exporter] [%(levelname)s] %(message)s', level=lvl)

    # Load config
    logging.info('Loading configuration file...')
    raw_config = load_config_from_file(args.config)
    if not raw_config:
        return 1

    # Run application
    logging.info('Starting application...')
    app = core.App(raw_config)
    app.start()

    # Exiting...
    logging.info('Bye bye...')
    return 0


# Main body
if __name__ == '__main__':
    exit(main())
