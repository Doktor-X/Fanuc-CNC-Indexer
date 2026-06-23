import json
import os
import sys


APP_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
CONFIG_FILE = os.path.join(APP_DIR, 'config.json')
TOOLS_FILE = os.path.join(APP_DIR, 'alati.json')

DEFAULT_CONFIG = {
    'default_folder': None,
    'column_widths': {},
    'last_sort': {},
    'column_order': None,
    'ignored_extensions': [],
    'language': 'hr',
    'tools_file': None,
}


def _default_config():
    return {
        key: value.copy() if isinstance(value, (dict, list)) else value
        for key, value in DEFAULT_CONFIG.items()
    }


def write_json_atomic(path, data):
    """Spremi JSON preko privremene datoteke."""
    temp_path = path + '.tmp'
    try:
        with open(temp_path, 'w', encoding='utf-8') as json_file:
            json.dump(data, json_file, ensure_ascii=False, indent=2)
        os.replace(temp_path, path)
    except Exception:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass
        raise


def load_config():
    config = _default_config()
    if not os.path.exists(CONFIG_FILE):
        return config

    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as config_file:
            data = json.load(config_file)
    except (OSError, json.JSONDecodeError):
        return config

    if isinstance(data, dict):
        for key in config:
            if key in data:
                config[key] = data[key]
    return config


def save_config(changes):
    config = load_config()
    config.update(changes)
    try:
        write_json_atomic(CONFIG_FILE, config)
    except OSError:
        pass


def load_setting(key, default=None):
    return load_config().get(key, default)


def save_setting(key, value):
    save_config({key: value})


def load_tools():
    tools_path = load_setting('tools_file') or TOOLS_FILE
    if not os.path.exists(tools_path):
        return {}
    try:
        with open(tools_path, 'r', encoding='utf-8') as tools_file:
            data = json.load(tools_file)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_tools(tools):
    tools_path = load_setting('tools_file') or TOOLS_FILE
    write_json_atomic(tools_path, tools)


def set_tools_file(path):
    save_setting('tools_file', os.path.abspath(path) if path else None)


def get_tools_file():
    return load_setting('tools_file') or TOOLS_FILE
