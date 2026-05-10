import time
import requests
import psutil
from src.extensions import socketio
from src.state import stats_cache, stats_lock, models_cache, save_stats_cache
from src.utils.config import load_config
from src.utils.helpers import get_gpu_info
from src.worker.manager import worker_manager


from concurrent.futures import ThreadPoolExecutor


def _fetch_user_data(headers):
    r = requests.get("https://aihorde.net/api/v2/find_user", headers=headers, timeout=10)
    if r.status_code == 200:
        user_data = r.json()
        records = user_data.get("records", {})
        fulfillment = records.get("fulfillment", {})
        total_jobs_user = fulfillment.get("image", 0) + fulfillment.get("text", 0)

        with stats_lock:
            stats_cache.update({
                "username": user_data.get("username"),
                "kudos": user_data.get("kudos"),
                "kudos_generated": user_data.get("kudos_details", {}).get("accumulated", 0),
                "worker_count": user_data.get("worker_count"),
                "worker_ids": user_data.get("worker_ids", []),
                "requests_fulfilled": total_jobs_user
            })
            return stats_cache.get("worker_ids", [])
    return []


def _fetch_worker(wid, headers):
    try:
        rw = requests.get(f"https://aihorde.net/api/v2/workers/{wid}", headers=headers, timeout=5)
        if rw.status_code == 200:
            return rw.json()
    except requests.RequestException:
        pass
    return None


def _process_worker_data(worker_ids, headers, dreamer_name):
    results = []
    if worker_ids:
        with ThreadPoolExecutor(max_workers=min(len(worker_ids), 10)) as executor:
            fetch_fn = lambda wid: _fetch_worker(wid, headers)
            results = list(executor.map(fetch_fn, worker_ids))

    total_uptime = 0
    matched_worker = None

    for w in results:
        if w:
            total_uptime += w.get("uptime", 0)
            if not matched_worker or w.get("name") == dreamer_name:
                matched_worker = w

    return total_uptime, matched_worker


def _update_final_stats(total_uptime, matched_worker):
    with stats_lock:
        stats_cache.update({
            "uptime": total_uptime,
            "last_sync": time.strftime("%H:%M:%S")
        })

        if total_uptime > 0:
            stats_cache["kudos_hr_avg"] = stats_cache.get("kudos_generated", 0) / (total_uptime / 3600)
        else:
            stats_cache["kudos_hr_avg"] = 0

        if matched_worker:
            stats_cache.update({
                "paused": matched_worker.get("paused"),
                "maintenance": matched_worker.get("maintenance"),
                "worker_kudos": matched_worker.get("kudos_details", {}).get("generated", 0)
            })

        if worker_manager.worker_start_time:
            stats_cache["session_uptime"] = int(time.time() - worker_manager.worker_start_time)
            stats_cache["worker_start_time"] = worker_manager.worker_start_time
        else:
            stats_cache["session_uptime"] = 0
            stats_cache["worker_start_time"] = None

        return stats_cache.copy()


def stats_poller():
    while True:
        config = load_config()
        api_key = config.get("api_key")
        if api_key and api_key != "0000000000":
            try:
                headers = {"apikey": api_key, "Client-Agent": "HordeUI:1.0.0:Github"}
                worker_ids = _fetch_user_data(headers)
                dreamer_name = config.get("dreamer_name")

                total_uptime, matched_worker = _process_worker_data(worker_ids, headers, dreamer_name)
                cache_copy = _update_final_stats(total_uptime, matched_worker)

                socketio.emit("stats_update", cache_copy)
                save_stats_cache()
            except requests.RequestException as e:
                print(f"Stats poll network error: {e}")
            except Exception as e:
                print(f"Stats poll generic error: {e}")

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
                    models_copy = list(models_cache)
                socketio.emit("models_update", models_copy)
        except requests.RequestException:
            pass
        except Exception:
            pass
        time.sleep(300)
