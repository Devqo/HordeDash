import os
import sys
import json
import yaml
import time
import signal
import threading
import subprocess
import collections
import requests
import psutil
import re
import secrets
from functools import wraps
from flask import Flask, render_template, jsonify, request, send_from_directory, session, redirect, url_for
from flask_socketio import SocketIO, emit
from pyngrok import ngrok

import argparse

# --- Configuration ---
BRIDGE_DATA_PATH = "horde-worker-reGen/bridgeData.yaml"
CACHE_PATH = "ui_cache.json"
PORT = 7860
LOG_MAXLEN = 500

# --- Argument Parsing ---
parser = argparse.ArgumentParser(description="HordeUI Control Center")
parser.add_argument("--password", help="Set the UI password (overrides UI_PASSWORD env var)")
parser.add_argument("--port", type=int, default=PORT, help=f"Port to run the UI on (default: {PORT})")
args, unknown = parser.parse_known_args()

app = Flask(__name__)

# --- Security & Authentication Setup ---
ui_password = args.password or os.getenv("UI_PASSWORD")
using_generated_password = False

if not ui_password:
    ui_password = secrets.token_hex(8)
    using_generated_password = True
    print("\n" + "="*60)
    print(f"⚠️  NO UI_PASSWORD SET. YOUR AUTO-GENERATED PASSWORD IS: {ui_password}")
    print("="*60 + "\n")

app.config['SECRET_KEY'] = ui_password
app.config['UI_PASSWORD'] = ui_password
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# Restrict CORS to prevent cross-site WebSocket hijacking
socketio = SocketIO(app, async_mode='threading')

# --- State & Locks ---
log_lock = threading.Lock()
stats_lock = threading.Lock()

log_buffer = collections.deque(maxlen=LOG_MAXLEN)
stats_cache = {}
models_cache =[]

worker_proc = None
worker_thread = None
worker_start_time = None

log_state = {
    "in_status_block": False,
    "current_status_block":[]
}

if os.path.exists(CACHE_PATH):
    try:
        with open(CACHE_PATH, "r") as f:
            stats_cache = json.load(f)
    except: pass

# --- Authentication Decorator ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('authenticated'):
            if request.path.startswith('/api/'):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@socketio.on('connect')
def handle_connect():
    if not session.get('authenticated'):
        return False

# --- Helpers ---
# Regexes from @carpfp (<@544620828214558721>)
RE_JOBS = re.compile(r"(?<=Jobs: )[^\n]+")
RE_JOBS_SUB = re.compile(r"(?<=<)[^?>]+")
RE_KUDOS = re.compile(r"(?<=Total Session Kudos: ).+(?= \|)")
RE_STATS = re.compile(r"(?<=Session job info: )[^\n]+")
RE_JOB_ID = re.compile(r"(?<=Starting inference for job )\S+")
RE_JOB_MODEL = re.compile(r"(?<=Model: )[^\n]+")
RE_JOB_DETAILS = re.compile(r"\d+x\d+ for \d+ steps with sampler \S+ for a batch of \d+")
RE_DREAMER = re.compile(r"(?<=dreamer_name: )\S+ \| \(v\d+\.\d+\.\d+\) \| horde user: \S+")

def is_remote_request():
    host = request.headers.get('Host', '').lower()
    # If accessed via ngrok or any non-local hostname
    if 'ngrok' in host:
        return True
    # If it has a proxy header, it's likely remote
    if request.headers.get('X-Forwarded-For'):
        return True
    return False

def strip_ansi_python(text):
    ansi_escape = re.compile(r'(?:\x1B[@-_][0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)

def parse_status_block(lines):
    processes =[]
    current_section = None
    for line in lines:
        line = line.strip()
        if "Process info:" in line:
            current_section = "process"
            continue
        elif "Job Info:" in line:
            current_section = "job"
            continue
        elif "Worker Info:" in line:
            current_section = "worker"
            continue
            
        if current_section == "process" and line.startswith("Process"):
            processes.append(line)
            
    if processes:
        with stats_lock:
            stats_cache["processes"] = processes
        socketio.emit("stats_update", stats_cache)

def save_stats_cache():
    try:
        with stats_lock:
            cache_copy = stats_cache.copy()
        with open(CACHE_PATH, "w") as f:
            json.dump(cache_copy, f)
    except: pass

def load_config():
    if not os.path.exists(BRIDGE_DATA_PATH):
        return {}
    with open(BRIDGE_DATA_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def save_config(new_config):
    if not os.path.exists(BRIDGE_DATA_PATH):
        with open(BRIDGE_DATA_PATH, "w", encoding="utf-8") as f:
            yaml.dump(new_config, f, default_flow_style=False, allow_unicode=True)
        return

    with open(BRIDGE_DATA_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()

    updated_lines = []
    seen_keys = set()
    skip_indented = False
    
    # Surgical replacement for top-level keys to preserve comments/formatting
    for line in lines:
        if skip_indented:
            # If line is indented more than 0 spaces, it's part of the block we're skipping
            if line.startswith(" ") or line.startswith("\t") or not line.strip():
                continue
            else:
                skip_indented = False

        # Match "key: value" or "key: # comment"
        match = re.match(r"^(\w+):\s*(.*)", line)
        if match:
            key = match.group(1)
            original_remainder = match.group(2).strip()
            if key in new_config:
                val = new_config[key]
                
                # Format value for YAML
                if isinstance(val, bool):
                    yaml_val = "true" if val else "false"
                elif isinstance(val, (int, float)):
                    yaml_val = str(val)
                elif val is None:
                    yaml_val = "null"
                else:
                    # Escape quotes if necessary
                    val_str = str(val).replace('"', '\\"')
                    yaml_val = f'"{val_str}"'
                
                # If the original was a block header (no value on same line), skip subsequent indented lines
                if not original_remainder:
                    skip_indented = True
                
                # Keep any trailing comment if it existed
                comment = ""
                if "#" in match.group(2):
                    comment = "  " + match.group(2)[match.group(2).find("#"):]
                
                updated_lines.append(f"{key}: {yaml_val}{comment}\n")
                seen_keys.add(key)
                continue
        updated_lines.append(line)
        
    # Append any keys from new_config that weren't found in the file
    new_keys_added = False
    for key, val in new_config.items():
        if key not in seen_keys:
            if not new_keys_added:
                updated_lines.append("\n## Added by HordeUI\n")
                new_keys_added = True
            
            if isinstance(val, bool):
                yaml_val = "true" if val else "false"
            elif isinstance(val, (int, float)):
                yaml_val = str(val)
            else:
                val_str = str(val).replace('"', '\\"')
                yaml_val = f'"{val_str}"'
            updated_lines.append(f"{key}: {yaml_val}\n")

    with open(BRIDGE_DATA_PATH, "w", encoding="utf-8") as f:
        f.writelines(updated_lines)

def get_gpu_info():
    try:
        result = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total,name", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, check=True
        )
        gpus =[]
        for line in result.stdout.strip().split("\n"):
            util, used, total, name = line.split(", ")
            gpus.append({
                "name": name,
                "util": int(util),
                "used": int(used),
                "total": int(total)
            })
        return gpus
    except:
        return[]

# --- Worker Management ---
def _tail_logs():
    if not worker_proc or not worker_proc.stdout:
        return
    
    print("Log tailing started...")
    while True:
        line = worker_proc.stdout.readline()
        if not line:
            if worker_proc.poll() is not None:
                break
            time.sleep(0.1)
            continue
            
        with log_lock:
            log_buffer.append(line)
        
        raw_line = strip_ansi_python(line)
        # Status block detection (flexible carets)
        if "^^^^" in raw_line and len(raw_line.strip().replace("^", "")) == 0:
            log_state["in_status_block"] = True
            log_state["current_status_block"] =[]
        elif "vvvv" in raw_line and len(raw_line.strip().replace("v", "")) == 0:
            log_state["in_status_block"] = False
            parse_status_block(log_state["current_status_block"])
        elif log_state["in_status_block"]:
            log_state["current_status_block"].append(raw_line)

            # Official Regex Parsing
            stats_match = RE_STATS.search(raw_line)
            if stats_match:
                try:
                    parts = stats_match.group(0).split("|")
                    for p in parts:
                        if "submitted:" in p:
                            stats_cache["session_jobs"] = int(p.split(":")[1].strip())
                except: pass

            kudos_match = RE_KUDOS.search(raw_line)
            if kudos_match:
                try:
                    # kudos_match.group(0) -> "2,741.51 over 28.95 minutes"
                    val = kudos_match.group(0).split("over")[0].strip().replace(",", "")
                    stats_cache["session_kudos"] = float(val)
                    # The rate is after the |, so RE_KUDOS doesn't catch it directly if it ends at |
                    # But we can extract it manually or refine RE_KUDOS
                    hr_part = raw_line.split("|")[-1]
                    if "Session:" in hr_part:
                        hr_val = hr_part.split(":")[1].split("(")[0].strip().replace(",", "")
                        stats_cache["session_kudos_hr"] = float(hr_val)
                except: pass

            job_id_match = RE_JOB_ID.search(raw_line)
            if job_id_match:
                stats_cache["current_job_id"] = job_id_match.group(0)
                stats_cache["activity_text"] = "Starting Inference"
                stats_cache["activity_subtext"] = f"Job ID: {job_id_match.group(0)}"
            
            job_model_match = RE_JOB_MODEL.search(raw_line)
            if job_model_match:
                stats_cache["current_job_model"] = job_model_match.group(0)
                stats_cache["activity_text"] = "Inference in Progress"
                stats_cache["activity_subtext"] = f"Model: {job_model_match.group(0)}"

            job_details_match = RE_JOB_DETAILS.search(raw_line)
            if job_details_match:
                stats_cache["current_job_details"] = job_details_match.group(0)
                stats_cache["activity_subtext"] += f" ({job_details_match.group(0)})"

            if "Inference finished for job" in raw_line:
                stats_cache["activity_text"] = "System Idle"
                stats_cache["activity_subtext"] = "Waiting for next job..."
                stats_cache["current_job_id"] = None

            if "Total Kudos Accumulated:" in raw_line:
                try:
                    val = raw_line.split(":")[1].split("(")[0].strip().replace(",", "")
                    stats_cache["kudos"] = float(val)
                except: pass

        socketio.emit("stats_update", stats_cache)
        socketio.emit("log", {"line": line})
    
    worker_proc.wait()
    socketio.emit("worker_status", {"running": False})
    print("Log tailing stopped.")

def start_worker(extra_args=""):
    global worker_proc, worker_thread, worker_start_time
    if worker_proc and worker_proc.poll() is None:
        return False
    
    worker_start_time = time.time()

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    if os.name == 'nt':
        if os.path.exists("horde-worker-reGen/horde-bridge.cmd"):
            cmd =["cmd", "/c", "horde-bridge.cmd"] + extra_args.split()
        else:
            cmd =[sys.executable, "-u", "run_worker.py"] + extra_args.split()
    else:
        cmd = ["bash", "./horde-bridge.sh"] + extra_args.split()

    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0

    worker_proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.PIPE,
        text=True, bufsize=1, universal_newlines=True, env=env,
        creationflags=creationflags, cwd="horde-worker-reGen"
    )
    
    try:
        worker_proc.stdin.write("\n")
        worker_proc.stdin.close()
    except (BrokenPipeError, OSError): 
        pass # Expected if process doesn't wait for stdin

    socketio.emit("worker_status", {"running": True}) 
    worker_thread = threading.Thread(target=_tail_logs, daemon=True)
    worker_thread.start()
    return True

def stop_worker(force=False):
    stopped = False
    
    target_pid = None
    if worker_proc and worker_proc.poll() is None:
        target_pid = worker_proc.pid
    else:
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = proc.info['cmdline']
                if cmdline and any('run_worker.py' in arg for arg in cmdline):
                    target_pid = proc.info['pid']
                    break
            except: continue

    if target_pid:
        if force:
            if os.name == 'nt':
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(target_pid)], capture_output=True)
            else:
                os.kill(target_pid, signal.SIGKILL)
            stopped = True
        else:
            if os.name == 'nt':
                os.kill(target_pid, signal.CTRL_C_EVENT)
            else:
                os.kill(target_pid, signal.SIGINT)
            return {"status": "stopping_gracefully"}

    if stopped:
        socketio.emit("worker_status", {"running": False})
        return {"status": "stopped"}
    return {"status": "no_worker_found"}

# --- Background Tasks ---
def stats_poller():
    while True:
        config = load_config()
        api_key = config.get("api_key")
        if api_key and api_key != "0000000000":
            try:
                headers = {"apikey": api_key, "Client-Agent": "HordeUI:1.0.0:Github"}
                r = requests.get("https://aihorde.net/api/v2/find_user", headers=headers, timeout=10)
                
                with stats_lock:
                    if r.status_code == 200:
                        data = r.json()
                        records = data.get("records", {})
                        fulfillment = records.get("fulfillment", {})
                        total_jobs_user = fulfillment.get("image", 0) + fulfillment.get("text", 0)
                        
                        stats_cache.update({
                            "username": data.get("username"),
                            "kudos": data.get("kudos"),
                            "kudos_generated": data.get("kudos_details", {}).get("accumulated", 0),
                            "worker_count": data.get("worker_count"),
                            "worker_ids": data.get("worker_ids", []),
                            "requests_fulfilled": total_jobs_user
                        })
                    
                    # Fetch detailed stats for each worker ID to get uptime (even if offline)
                    worker_ids = stats_cache.get("worker_ids", [])
                    total_uptime = 0
                    dreamer_name = config.get("dreamer_name")
                    matched_worker = None

                    for wid in worker_ids:
                        rw = requests.get(f"https://aihorde.net/api/v2/workers/{wid}", headers=headers, timeout=5)
                        if rw.status_code == 200:
                            w = rw.json()
                            total_uptime += w.get("uptime", 0)
                            # If we don't have a matched worker yet, or this one matches the dreamer_name
                            if not matched_worker or w.get("name") == dreamer_name:
                                matched_worker = w

                    stats_cache.update({
                        "uptime": total_uptime,
                        "last_sync": time.strftime("%H:%M:%S")
                    })

                    # Calculate average based on lifetime accumulated kudos / total uptime
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
                    
                    if worker_start_time:
                        stats_cache["session_uptime"] = int(time.time() - worker_start_time)
                        stats_cache["worker_start_time"] = worker_start_time
                    else:
                        stats_cache["session_uptime"] = 0
                        stats_cache["worker_start_time"] = None

                socketio.emit("stats_update", stats_cache)
                save_stats_cache()
            except Exception as e:
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
        except: pass
        time.sleep(2)

def models_poller():
    global models_cache
    while True:
        try:
            r = requests.get("https://aihorde.net/api/v2/status/models?type=image", timeout=20)
            if r.status_code == 200:
                models_cache = r.json()
                socketio.emit("models_update", models_cache)
        except: pass
        time.sleep(300)

# --- Routes ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        pwd = request.form.get('password')
        if pwd == app.config['UI_PASSWORD']:
            session['authenticated'] = True
            return redirect(url_for('index'))
        return render_template('login.html', error="Invalid password", using_generated_password=using_generated_password)
    return render_template('login.html', using_generated_password=using_generated_password)

@app.route('/logout')
def logout():
    session.pop('authenticated', None)
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    return render_template('index.html')

@app.route('/api/config', methods=['GET', 'POST'])
@login_required
def api_config():
    if request.method == 'POST':
        new_data = request.json
        # Filter out masked keys so we don't overwrite real keys with "********"
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

@app.route('/api/models', methods=['GET'])
@login_required
def api_models():
    return jsonify(models_cache)

@app.route('/api/logs', methods=['GET'])
@login_required
def api_logs():
    with log_lock:
        logs = list(log_buffer)
    return jsonify(logs)

@app.route('/api/worker/status')
@login_required
def api_worker_status():
    global worker_start_time
    is_running = worker_proc is not None and worker_proc.poll() is None
    
    if not is_running:
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = proc.info['cmdline']
                if cmdline and any('run_worker.py' in arg for arg in cmdline):
                    is_running = True
                    try:
                        worker_start_time = proc.create_time()
                    except: pass
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

    return jsonify({"running": is_running})

@app.route('/api/stats')
@login_required
def api_stats():
    with stats_lock:
        return jsonify(stats_cache)

@app.route('/api/stats/clear', methods=['POST'])
@login_required
def api_stats_clear():
    global stats_cache
    with stats_lock:
        stats_cache = {
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
        }
    if os.path.exists(CACHE_PATH):
        os.remove(CACHE_PATH)
    return jsonify({"status": "success", "message": "Cache cleared"})

@app.route('/api/worker/start', methods=['POST'])
@login_required
def api_worker_start():
    args = request.json.get("args", "")
    if start_worker(args):
        return jsonify({"status": "started"})
    return jsonify({"status": "already_running"}), 400

@app.route('/api/worker/stop', methods=['POST'])
@login_required
def api_worker_stop():
    force = request.json.get('force', False)
    return jsonify(stop_worker(force=force))

@app.route('/api/update', methods=['POST'])
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

@app.route('/api/download_models', methods=['POST'])
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

@app.route('/api/ngrok', methods=['POST'])
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

if __name__ == '__main__':
    threading.Thread(target=stats_poller, daemon=True).start()
    threading.Thread(target=sysinfo_poller, daemon=True).start()
    threading.Thread(target=models_poller, daemon=True).start()
    
    print(f"HordeUI running at http://0.0.0.0:{PORT}")
    socketio.run(app, host='0.0.0.0', port=PORT, debug=False)