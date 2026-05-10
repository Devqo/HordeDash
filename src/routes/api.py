import os
import sys
import threading
import subprocess
from flask import Blueprint, jsonify, request
from pyngrok import ngrok
import psutil

from src.utils.decorators import login_required
from src.utils.config import load_config, save_config
from src.utils.helpers import is_remote_request
from src.state import log_lock, log_buffer, stats_lock, stats_cache, CACHE_PATH, PORT, models_cache
from src.worker.manager import worker_manager
from src.extensions import socketio

api_bp = Blueprint('api', __name__, url_prefix='/api')

@api_bp.route('/config', methods=['GET', 'POST'])
@login_required
def api_config():
    if request.method == 'POST':
        new_data = request.json
        filtered_data = {k: v for k, v in new_data.items() if v != "********"}
        save_config(filtered_data)
        return jsonify({"status": "success"})
    
    config = load_config()
    if is_remote_request():
        sensitive_keys = ["api_key", "civitai_api_token", "ngrok_authtoken"]
        for key in sensitive_keys:
            if key in config:
                config[key] = "********"
    return jsonify(config)

@api_bp.route('/models', methods=['GET'])
@login_required
def api_models():
    return jsonify(models_cache)

@api_bp.route('/logs', methods=['GET'])
@login_required
def api_logs():
    with log_lock:
        logs = list(log_buffer)
    return jsonify(logs)

@api_bp.route('/worker/status')
@login_required
def api_worker_status():
    is_running = worker_manager.worker_proc is not None and worker_manager.worker_proc.poll() is None
    
    if not is_running:
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = proc.info['cmdline']
                if cmdline and any('run_worker.py' in arg for arg in cmdline):
                    is_running = True
                    try:
                        worker_manager.worker_start_time = proc.create_time()
                    except: pass
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

    return jsonify({"running": is_running})

@api_bp.route('/stats')
@login_required
def api_stats():
    with stats_lock:
        return jsonify(stats_cache)

@api_bp.route('/stats/clear', methods=['POST'])
@login_required
def api_stats_clear():
    with stats_lock:
        stats_cache.clear()
        stats_cache.update({
            "username": "N/A",
            "kudos": 0,
            "kudos_generated": 0,
            "worker_count": 0,
            "uptime": 0,
            "requests_fulfilled": 0,
            "kudos_hr_avg": 0,
            "session_uptime": 0,
            "session_jobs": 0,
            "worker_start_time": None
        })
    if os.path.exists(CACHE_PATH):
        try:
            os.remove(CACHE_PATH)
        except: pass
    return jsonify({"status": "success", "message": "Cache cleared"})

@api_bp.route('/worker/start', methods=['POST'])
@login_required
def api_worker_start():
    args = request.json.get("args", "")
    if worker_manager.start_worker(args):
        return jsonify({"status": "started"})
    return jsonify({"status": "already_running"}), 400

@api_bp.route('/worker/stop', methods=['POST'])
@login_required
def api_worker_stop():
    force = request.json.get('force', False)
    return jsonify(worker_manager.stop_worker(force=force))

@api_bp.route('/update', methods=['POST'])
@login_required
def api_update():
    def run_update():
        socketio.emit("log", {"line": ">>> Starting update...\n"})
        try:
            process = subprocess.Popen(["git", "pull"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.PIPE, text=True, cwd="horde-worker-reGen")
            try:
                process.stdin.write("\n")
                process.stdin.close()
            except: pass
            for line in process.stdout: socketio.emit("log", {"line": f"[GIT] {line}"})
            process.wait()
            
            update_cmd = "update-runtime.cmd" if os.name == 'nt' else "./update-runtime.sh"
            if os.path.exists(f"horde-worker-reGen/{update_cmd}"):
                process = subprocess.Popen([update_cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.PIPE, text=True, cwd="horde-worker-reGen")
                try:
                    process.stdin.write("\n")
                    process.stdin.close()
                except: pass
                for line in process.stdout: socketio.emit("log", {"line": f"[UPDATE] {line}"})
                process.wait()
            socketio.emit("log", {"line": ">>> Update complete.\n"})
            socketio.emit("maintenance_complete", {"status": "success"})
        except Exception as e:
            socketio.emit("log", {"line": f">>> Update failed: {e}\n"})
            socketio.emit("maintenance_complete", {"status": "error"})
            
    threading.Thread(target=run_update).start()
    return jsonify({"status": "update_triggered"})

@api_bp.route('/download_models', methods=['POST'])
@login_required
def api_download_models():
    def run_download():
        socketio.emit("log", {"line": ">>> Starting model download...\n"})
        try:
            process = subprocess.Popen([sys.executable, "download_models.py"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.PIPE, text=True, cwd="horde-worker-reGen")
            try:
                process.stdin.write("\n")
                process.stdin.close()
            except: pass
            for line in process.stdout: socketio.emit("log", {"line": f"[DOWNLOAD] {line}"})
            process.wait()
            socketio.emit("log", {"line": ">>> Download complete.\n"})
            socketio.emit("maintenance_complete", {"status": "success"})
        except Exception as e:
            socketio.emit("log", {"line": f">>> Download failed: {e}\n"})
            socketio.emit("maintenance_complete", {"status": "error"})
            
    threading.Thread(target=run_download).start()
    return jsonify({"status": "download_triggered"})

@api_bp.route('/ngrok', methods=['POST'])
@login_required
def api_ngrok():
    action = request.json.get("action")
    if action == "start":
        try:
            config = load_config()
            authtoken = config.get("ngrok_authtoken")
            if authtoken:
                ngrok.set_auth_token(authtoken)
            
            port = request.json.get("port", PORT)
            tunnel = ngrok.connect(port)
            return jsonify({"status": "success", "url": tunnel.public_url})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500
    else:
        try:
            tunnels = ngrok.get_tunnels()
            for t in tunnels:
                ngrok.disconnect(t.public_url)
            return jsonify({"status": "success"})
        except:
            return jsonify({"status": "error", "message": "Failed to stop tunnel"}), 500
