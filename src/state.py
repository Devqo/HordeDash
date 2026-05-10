import threading
import collections
import json
import os

PORT = 7860
BRIDGE_DATA_PATH = "horde-worker-reGen/bridgeData.yaml"
CACHE_PATH = "ui_cache.json"
LOG_MAXLEN = 500

log_lock = threading.Lock()
stats_lock = threading.Lock()

log_buffer = collections.deque(maxlen=LOG_MAXLEN)
stats_cache = {}
models_cache = []

log_state = {
    "in_status_block": False,
    "current_status_block": []
}


def load_stats_cache():
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, "r") as f:
                data = json.load(f)
                with stats_lock:
                    stats_cache.update(data)
        except (json.JSONDecodeError, OSError):
            pass


def save_stats_cache():
    try:
        with stats_lock:
            cache_copy = stats_cache.copy()
        with open(CACHE_PATH, "w") as f:
            json.dump(cache_copy, f)
    except (OSError, TypeError):
        pass
