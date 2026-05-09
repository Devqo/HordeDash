## HordeUI — implementation plan for horde-worker-reGen

### Overview & philosophy

The goal is a **self-contained Python web app** (`horde_ui.py`) that sits alongside the worker directory, reads/writes `bridgeData.yaml` directly, manages the worker subprocess, and proxies the AI Horde REST API for live stats. Zero modifications to the worker's own code are required. The UI is served over HTTP and is reachable from any device on the LAN.

---

### Tech stack

**Backend:** Python 3.10+ with `Flask` + `Flask-SocketIO` (eventlet). This gives real-time log streaming via WebSocket without any additional infrastructure. The entire backend lives in a single `horde_ui.py` file with a `templates/` folder for the HTML.

**Frontend:** Vanilla JS + a small amount of Tailwind CDN for styling. No build step, no Node required. A single `index.html` template rendered by Flask.

**Dependencies (pip-installable):**
- `flask`, `flask-socketio`, `eventlet` — server + WebSockets
- `pyyaml` — read/write `bridgeData.yaml`
- `requests` — AI Horde API + CivitAI API polling
- `psutil` — GPU/CPU/RAM stats (optional)
- `pyngrok` — optional WAN tunnel

Zero of these are in the worker's own requirements, so there's no conflict.

---

### File layout

```
horde-worker-reGen/
├── bridgeData.yaml           ← worker config (already exists)
├── horde_ui.py               ← THE ONLY NEW FILE (main entry point)
├── templates/
│   └── index.html            ← single-page UI template
├── static/
│   └── ui.js                 ← optional split-out JS
└── start_ui.sh / start_ui.cmd ← one-click launchers
```

The `start_ui.sh` / `start_ui.cmd` scripts are 2-line launchers:
```bash
#!/bin/bash
python horde_ui.py
```
That's it. The user double-clicks it, the UI is live at `http://0.0.0.0:7860`.

---

### Backend design (`horde_ui.py`)

#### 1. Config manager

Reads `bridgeData.yaml` on every GET, writes it atomically on every POST. The worker auto-picks up changes within ~60 seconds without restarting.

Key fields mapped to UI controls (derived from the `horde-sdk` `BridgeData` schema):

| YAML key | UI control | Notes |
|---|---|---|
| `dreamer_name` | Text input | Worker name |
| `api_key` | Password input | AI Horde API key |
| `civitai_api_key` | Password input | CivitAI API key |
| `models_to_load` | Multi-select + magic strings | Supports `"top 10"`, `"ALL MODELS"`, etc. |
| `models_to_skip` | Multi-select | Models to block |
| `max_power` | Slider (1–512) | Pixel budget |
| `queue_size` | Slider (0–3) | RAM-sensitive |
| `max_threads` | Slider (1–4) | VRAM-sensitive |
| `max_batch` | Slider (1–16) | |
| `high_memory_mode` | Toggle | |
| `high_performance_mode` | Toggle | |
| `moderate_performance_mode` | Toggle | |
| `low_vram_mode` | Toggle | |
| `safety_on_gpu` | Toggle | |
| `allow_nsfw` | Toggle | |
| `allow_img2img` | Toggle | |
| `allow_painting` | Toggle | |
| `allow_post_processing` | Toggle | |
| `allow_controlnet` | Toggle | |
| `allow_sdxl_controlnet` | Toggle | |
| `allow_lora` | Toggle | |
| `extra_slow_worker` | Toggle | |
| `limit_max_steps` | Toggle | |
| `unload_models_from_vram_often` | Toggle | |
| `post_process_job_overlap` | Toggle | |
| `ram_to_leave_free` | Text input | `80%` or MB |
| `vram_to_leave_free` | Text input | `80%` or MB |
| `max_lora_cache_size` | Slider (10–2048 GB) | |
| `preload_timeout` | Number input | Seconds |
| `number_of_dynamic_models` | Number input | |
| `max_models_to_download` | Number input | |
| `extra_cmdline_args` | Text input | Custom CLI flags |

#### 2. Process manager

```python
import subprocess, threading, collections, signal

worker_proc = None
log_buffer = collections.deque(maxlen=500)  # ring buffer

def start_worker(extra_args=""):
    global worker_proc
    cmd = ["./horde-bridge.sh"] + extra_args.split()
    worker_proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1
    )
    threading.Thread(target=_tail_logs, daemon=True).start()

def _tail_logs():
    for line in worker_proc.stdout:
        log_buffer.append(line)
        socketio.emit("log", {"line": line})  # real-time push

def stop_worker():
    if worker_proc:
        worker_proc.send_signal(signal.SIGINT)  # graceful Ctrl+C
```

The worker process is a child of the UI process. Logs are tailed line-by-line in a background thread and pushed to all connected browsers via SocketIO. The UI's own process stays alive independently.

#### 3. Stats poller

A background thread polls the AI Horde REST API every 10 seconds using the stored API key:

```
GET https://aihorde.net/api/v2/find_user  →  kudos, username
GET https://aihorde.net/api/v2/workers/{id}  →  kudos/hr, uptime, jobs, current job, queue
```

Results are cached in memory and pushed to clients via SocketIO on change.

#### 4. Model catalogue

On startup, fetch the full model list from the AI Horde:
```
GET https://aihorde.net/api/v2/status/models?type=image
```
This gives every model name, queue depth, and worker count. Used to populate the model dropdowns. The "top N" button simply sorts by queue depth and selects the first N.

#### 5. WebSocket events (SocketIO)

| Event | Direction | Payload |
|---|---|---|
| `log` | server→client | `{line: str}` |
| `stats` | server→client | `{kudos, kudos_hr, uptime, jobs, current_job, queue}` |
| `worker_status` | server→client | `{running: bool, pid: int}` |
| `sysinfo` | server→client | `{cpu_pct, ram_pct, gpu_pct, vram_free_gb}` |
| `start_worker` | client→server | `{args: str}` |
| `stop_worker` | client→server | `{}` |

#### 6. REST endpoints (Flask)

| Method | Route | Description |
|---|---|---|
| `GET` | `/api/config` | Returns parsed `bridgeData.yaml` as JSON |
| `POST` | `/api/config` | Writes new config to `bridgeData.yaml` |
| `POST` | `/api/update` | Runs `git pull` + `update-runtime.sh` in subprocess |
| `POST` | `/api/download_models` | Runs `download_models.py` in subprocess |
| `GET` | `/api/models` | Returns full model list from Horde API |
| `GET` | `/api/logs` | Returns last N log lines from the ring buffer |
| `GET` | `/api/sysinfo` | Returns live CPU/RAM/GPU via psutil |
| `POST` | `/api/ngrok` | Starts/stops ngrok tunnel, returns public URL |
| `POST` | `/api/test_generate` | Submits a test generation to the Horde API |

#### 7. Auth middleware (optional)

If `UI_PASSWORD` env var is set, Flask checks a session cookie before serving any page. A `/login` route accepts the password and sets the cookie. This protects the UI when exposed via ngrok.

---

### Frontend design

A single-page app with five tabs:

**Tab 1 — Dashboard**
Live stats panel: kudos/hr, session kudos, account kudos, uptime, jobs completed, current job, queue length. Worker start/stop button with status indicator. Real-time log console (auto-scrolling `<pre>` fed by SocketIO). Optional: CPU/RAM/GPU bar gauges via `psutil`.

**Tab 2 — Configuration**
All `bridgeData.yaml` fields in logical groups: Identity, API Keys, Models, Performance, Behaviour, Advanced. Save button writes the YAML. A "Validate" button can pre-check obvious conflicts (e.g. `max_threads: 2` with `max_power: 64`).

**Tab 3 — Models**
A searchable table of all Horde models with queue depth. "Select top N" dropdown pre-selects the N most-requested models. Separate "models to skip" multi-select. Models are tagged with their baseline (SD1.5, SDXL, Flux, etc.) for easy filtering.

**Tab 4 — Maintenance**
"Update worker" button (git pull + update-runtime). "Download models" button (runs `download_models.py` with live output streamed in). "Test generation" form — enter a prompt, click Generate, get an image back via the Horde API using your key.

**Tab 5 — Advanced (optional)**
Multi-worker management (launch additional instances with `CUDA_VISIBLE_DEVICES=N`). ngrok toggle with public URL display. Extra CLI arguments freeform input. GPU selector (NVIDIA: reads from `nvidia-smi` output).

---

### Key implementation details

**YAML atomic write:** Write to `bridgeData.yaml.tmp`, then `os.replace()` to avoid corrupting the file mid-write while the worker is watching it.

**"Top N" model logic:** Fetch model queue counts from the Horde API, sort descending by `queued` field, take first N names, set `models_to_load: ["top N"]` in the YAML (the worker accepts this magic string natively) OR pre-expand the list — both are supported.

**Log parsing for live stats:** The worker logs lines like `Kudos/hour: 4523` and `Job completed in 12.3s` to stdout. A simple regex pass over the log ring buffer extracts these for the dashboard without needing to touch the worker's code.

**Worker detection:** On UI startup, scan `ps aux` / `psutil.process_iter()` for an existing `horde-bridge` process. If found, attach to its PID and tail its log file from the `logs/` directory instead of spawning a new one.

**ngrok integration:**
```python
from pyngrok import ngrok
tunnel = ngrok.connect(7860, "http")
public_url = tunnel.public_url
```
One function call, no config. The public URL is shown in the UI header and can be QR-coded.

**GPU stats:**
```python
import subprocess
result = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,memory.free,memory.total",
                         "--format=csv,noheader,nounits"], capture_output=True, text=True)
```
Falls back gracefully if `nvidia-smi` is absent (AMD/CPU workers).

**Cross-platform launcher scripts:**

`start_ui.sh`:
```bash
#!/bin/bash
cd "$(dirname "$0")"
python horde_ui.py
```

`start_ui.cmd`:
```bat
@echo off
cd /d "%~dp0"
python horde_ui.py
pause
```

---

### Startup flow---

### Requirements checklist vs bounty spec

| Requirement | Implementation |
|---|---|
| Start/stop worker | `POST /api/worker/start` + `stop` via SocketIO, `subprocess.Popen` + `SIGINT` |
| Set worker name | `dreamer_name` field → `bridgeData.yaml` write |
| Set models offered (dropdown) | Multi-select from live Horde model catalogue |
| "Top N" models | Dropdown (1–20), pre-fills `models_to_load: ["top N"]` |
| Set models to NOT offer | `models_to_skip` multi-select |
| All performance settings | Sliders/toggles for all fields in the SDK schema |
| CivitAI API key | Password input → YAML |
| Perform updates | Button → `git pull` + `update-runtime.sh` |
| AI Horde API key | Password input → YAML |
| Kudos/hr, session kudos, account kudos, uptime | Polled from Horde API every 10s via SocketIO |
| Current job + job queue | Parsed from live log stream + worker API endpoint |
| Completed jobs this session | Counted from log events |
| Run `download_models.py` | Button → subprocess with live output |
| Custom CLI arguments | Free-text field appended to launch command |
| Accessible from LAN | Flask bound to `0.0.0.0:7860` |
| Easy to start | Double-click `start_ui.sh` / `.cmd` |
| Minimal worker code modification | Zero — UI is a completely separate process |
| **Optional:** ngrok WAN access | `pyngrok` one-liner, toggled from UI |
| **Optional:** GPU selection | `CUDA_VISIBLE_DEVICES=N` prepended to launch command |
| **Optional:** Test generation | Submit prompt to Horde API, display result image |
| **Optional:** Password protection | Session cookie auth if `UI_PASSWORD` env var set |
| **Optional:** Multi-worker | Launch multiple instances with different GPUs and names, each gets its own card on the dashboard |
| **Optional:** CPU/RAM/GPU usage | `psutil` + `nvidia-smi` polled every 2s |

---

### Development sequence (recommended build order)

1. `horde_ui.py` skeleton — Flask server, bind, serve `index.html`
2. Config read/write — YAML load → JSON API → YAML save
3. Basic UI — all config fields rendered, Save works
4. Process manager — start/stop subprocess, log ring buffer
5. Log streaming — SocketIO `log` events, console in UI
6. Stats poller — Horde API → SocketIO `stats` events → dashboard cards
7. Model catalogue — fetch, search, "top N" selector
8. Maintenance tab — update + download_models with live output
9. Optional features — ngrok, GPU selection, test generation, auth, multi-worker, sysinfo