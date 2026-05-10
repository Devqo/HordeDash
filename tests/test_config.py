import os
import tempfile
import pytest
import yaml
from src.utils.config import save_config, load_config
import src.utils.config

@pytest.fixture
def temp_bridge_data(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".yaml")
    os.close(fd)
    monkeypatch.setattr(src.utils.config, 'BRIDGE_DATA_PATH', path)
    yield path
    os.remove(path)

def test_save_and_load_config_empty_file(temp_bridge_data):
    test_config = {
        "api_key": "test_key_123",
        "worker_count": 2,
        "is_active": True
    }
    
    save_config(test_config)
    loaded = load_config()
    
    assert loaded["api_key"] == "test_key_123"
    assert loaded["worker_count"] == 2
    assert loaded["is_active"] is True

def test_save_config_updates_existing_and_preserves_others(temp_bridge_data):
    # Write initial state
    with open(temp_bridge_data, "w") as f:
        f.write("api_key: old_key\nworker_count: 1\nother_setting: unchanged\n")
        
    update = {"api_key": "new_key", "new_setting": "value"}
    save_config(update)
    
    loaded = load_config()
    assert loaded["api_key"] == "new_key"
    assert loaded["worker_count"] == 1  # Unchanged since not in update
    assert loaded["other_setting"] == "unchanged" # Unchanged
    assert loaded["new_setting"] == "value" # New key added
