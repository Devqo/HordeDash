import time
import requests
import psutil
from src.extensions import socketio
from src.state import stats_cache, stats_lock, models_cache, save_stats_cache
from src.utils.config import load_config
from src.utils.helpers import get_gpu_info
from src.worker.manager import worker_manager


def stats_poller():
    while True:
        config = load_config()
        api_key = config.get("api_key")
        if api_key and api_key != "0000000000":
            try:
                headers = {"apikey": api_key,
                           "Client-Agent": "HordeUI:1.0.0:Github"}
                r = requests.get(
                    "https://aihorde.net/api/v2/find_user", headers=headers, timeout=10)

                with stats_lock:
                    if r.status_code == 200:
                        data = r.json()
                        records = data.get("records", {})
                        fulfillment = records.get("fulfillment", {})
                        total_jobs_user = fulfillment.get(
                            "image", 0) + fulfillment.get("text", 0)

                        stats_cache.update({
                            "username": data.get("username"),
                            "kudos": data.get("kudos"),
                            "kudos_generated": data.get("kudos_details", {}).get("accumulated", 0),
                            "worker_count": data.get("worker_count"),
                            "worker_ids": data.get("worker_ids", []),
                            "requests_fulfilled": total_jobs_user
                        })

                    worker_ids = stats_cache.get("worker_ids", [])
                    total_uptime = 0
                    dreamer_name = config.get("dreamer_name")
                    matched_worker = None

                    for wid in worker_ids:
                        rw = requests.get(
                            f"https://aihorde.net/api/v2/workers/{wid}", headers=headers, timeout=5)
                        if rw.status_code == 200:
                            w = rw.json()
                            total_uptime += w.get("uptime", 0)
                            if not matched_worker or w.get("name") == dreamer_name:
                                matched_worker = w

                    stats_cache.update({
                        "uptime": total_uptime,
                        "last_sync": time.strftime("%H:%M:%S")
                    })

                    if total_uptime > 0:
                        stats_cache["kudos_hr_avg"] = stats_cache.get(
                            "kudos_generated", 0) / (total_uptime / 3600)
                    else:
                        stats_cache["kudos_hr_avg"] = 0

                    if matched_worker:
                        stats_cache.update({
                            "paused": matched_worker.get("paused"),
                            "maintenance": matched_worker.get("maintenance"),
                            "worker_kudos": matched_worker.get("kudos_details", {}).get("generated", 0)
                        })

                    if worker_manager.worker_start_time:
                        stats_cache["session_uptime"] = int(
                            time.time() - worker_manager.worker_start_time)
                        stats_cache["worker_start_time"] = worker_manager.worker_start_time
                    else:
                        stats_cache["session_uptime"] = 0
                        stats_cache["worker_start_time"] = None

                socketio.emit("stats_update", stats_cache)
                save_stats_cache()
            except requests.RequestException as e:
                print(f"Stats poll error: {e}")

        time.sleep(30)


def sysinfo_poller():
    while True:
        try:
            info = {
                "cpu_pct": psutil.cpu_percent(),
                "ram_pct": psutil.virtual_memory().percent,
                "gpus": get_gpu_info()
            }
            socketio.emit("sysinfo", info)
        except (psutil.Error, ValueError, KeyError):
            pass
        time.sleep(2)


def models_poller():
    while True:
        try:
            r = requests.get(
                "https://aihorde.net/api/v2/status/models?type=image", timeout=20)
            if r.status_code == 200:
                with stats_lock:
                    # In-place mutation preserves references held by other modules
                    models_cache.clear()
                    models_cache.extend(r.json())
                socketio.emit("models_update", models_cache)
        except requests.RequestException:
            pass
        time.sleep(300)
