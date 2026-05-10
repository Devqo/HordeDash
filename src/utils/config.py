import os
from filelock import FileLock
from ruamel.yaml import YAML
from src.state import BRIDGE_DATA_PATH

yaml = YAML()
yaml.preserve_quotes = True
yaml.indent(mapping=2, sequence=4, offset=2)

DASHBOARD_KEYS = ["ngrok_authtoken", "UI_PASSWORD", "PORT"]
CONFIG_LOCK_PATH = "config.lock"
config_lock = FileLock(CONFIG_LOCK_PATH)


def _update_env_file(key, value):
    """Internal: update or add a key-value pair in the .env file without locking."""
    lines = []
    if os.path.exists(".env"):
        with open(".env", "r") as f:
            lines = f.readlines()

    found = False
    new_line = f"{key}={value}\n"
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = new_line
            found = True
            break

    if not found:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(new_line)

    with open(".env", "w") as f:
        f.writelines(lines)


def update_env_file(key, value):
    """Surgically update or add a key-value pair in the .env file."""
    with config_lock:
        _update_env_file(key, value)


def load_config():
    """Load config from both bridgeData.yaml and .env."""
    with config_lock:
        config = {}
        if os.path.exists(BRIDGE_DATA_PATH):
            with open(BRIDGE_DATA_PATH, "r", encoding="utf-8") as f:
                config = yaml.load(f) or {}

        # Overlay dashboard-specific keys from .env
        for key in DASHBOARD_KEYS:
            val = os.getenv(key)
            if val:
                config[key] = val

        return config


def save_config(new_config):
    """Save config, directing keys to bridgeData.yaml or .env as appropriate."""
    with config_lock:
        os.makedirs(os.path.dirname(BRIDGE_DATA_PATH), exist_ok=True)

        # 1. Handle Dashboard Keys (.env)
        for key in DASHBOARD_KEYS:
            if key in new_config:
                _update_env_file(key, new_config[key])
                # Set the env var in the current process so load_config sees it immediately
                os.environ[key] = str(new_config[key])

        # 2. Handle Worker Keys (YAML)
        yaml_config = {}
        if os.path.exists(BRIDGE_DATA_PATH):
            with open(BRIDGE_DATA_PATH, "r", encoding="utf-8") as f:
                yaml_config = yaml.load(f) or {}

        # Filter out dashboard keys from the YAML to avoid unrecognized key warnings
        filtered_updates = {
            k: v for k, v in new_config.items() if k not in DASHBOARD_KEYS
        }

        def deep_update(base, updates):
            for k, v in updates.items():
                if isinstance(v, dict) and k in base and isinstance(base[k], dict):
                    deep_update(base[k], v)
                else:
                    base[k] = v

        deep_update(yaml_config, filtered_updates)

        # Clean up the YAML: explicitly remove any leftover dashboard keys
        # that might have been there from previous versions
        for key in DASHBOARD_KEYS:
            yaml_config.pop(key, None)

        with open(BRIDGE_DATA_PATH, "w", encoding="utf-8") as f:
            yaml.dump(yaml_config, f)
