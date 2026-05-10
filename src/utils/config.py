import os
from ruamel.yaml import YAML
from src.state import BRIDGE_DATA_PATH

yaml = YAML()
yaml.preserve_quotes = True
yaml.indent(mapping=2, sequence=4, offset=2)

def load_config():
    if not os.path.exists(BRIDGE_DATA_PATH):
        return {}
    with open(BRIDGE_DATA_PATH, "r", encoding="utf-8") as f:
        return yaml.load(f) or {}

def save_config(new_config):
    # Ensure the directory exists
    os.makedirs(os.path.dirname(BRIDGE_DATA_PATH), exist_ok=True)
    
    config_to_save = {}
    if os.path.exists(BRIDGE_DATA_PATH):
        with open(BRIDGE_DATA_PATH, "r", encoding="utf-8") as f:
            config_to_save = yaml.load(f) or {}
    
    # Update with new values
    def deep_update(base, updates):
        for k, v in updates.items():
            if isinstance(v, dict) and k in base and isinstance(base[k], dict):
                deep_update(base[k], v)
            else:
                base[k] = v
                
    deep_update(config_to_save, new_config)
    
    with open(BRIDGE_DATA_PATH, "w", encoding="utf-8") as f:
        yaml.dump(config_to_save, f)
