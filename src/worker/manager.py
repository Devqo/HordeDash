import os
import sys
import time
import signal
import threading
import subprocess
import psutil
import re

from src.extensions import socketio
from src.state import stats_cache, stats_lock, log_buffer, log_lock, log_state
from src.utils.helpers import strip_ansi_python

# Regexes (partially) from @carpfp
RE_JOBS = re.compile(r"(?<=Jobs: )[^\n]+")
RE_JOBS_SUB = re.compile(r"(?<=<)[^?>]+")
RE_KUDOS = re.compile(r"Total Session Kudos: ([\d,.]+)")
RE_KUDOS_HR = re.compile(r"Session: ([\d,.]+)")
RE_STATS = re.compile(r"Session job info: ([^\n]+)")
RE_JOB_ID = re.compile(r"(?<=Starting inference for job )\S+")
RE_JOB_MODEL = re.compile(r"(?<=Model: )[^\n]+")
RE_JOB_DETAILS = re.compile(
    r"\d+x\d+ for \d+ steps with sampler \S+ for a batch of \d+")
RE_DREAMER = re.compile(
    r"(?<=dreamer_name: )\S+ \| \(v\d+\.\d+\.\d+\) \| horde user: \S+")
RE_LIVE_STATUS = re.compile(r"\[ ([\+\-\%]) \]:(\d+)")


def parse_status_block(lines):
    processes = []
    current_section = None
    for line in lines:
        content = line.split(" - ", 1)[1] if " - " in line else line
        content = content.strip()

        if "Process info:" in content:
            current_section = "process"
            continue
        elif "Job Info:" in content:
            current_section = "job"
            continue
        elif "Worker Info:" in content:
            current_section = "worker"
            continue

        if current_section == "process" and "Process " in content:
            processes.append(content)

    if processes:
        with stats_lock:
            stats_cache["processes"] = processes
        socketio.emit("stats_update", stats_cache)


class WorkerManager:
    def __init__(self):
        self.worker_proc = None
        self.worker_thread = None
        self.worker_start_time = None

    def _tail_logs(self):  # noqa: C901
        if not self.worker_proc or not self.worker_proc.stdout:
            return

        print("Log tailing started...")
        while True:
            line = self.worker_proc.stdout.readline()
            if not line:
                if self.worker_proc.poll() is not None:
                    break
                time.sleep(0.1)
                continue

            with log_lock:
                log_buffer.append(line)

            raw_line = strip_ansi_python(line)

            # Status block detection
            if "^^^^" in raw_line:
                log_state["in_status_block"] = True
                log_state["current_status_block"] = []
            elif "vvvv" in raw_line:
                log_state["in_status_block"] = False
                parse_status_block(log_state["current_status_block"])
            elif log_state["in_status_block"]:
                log_state["current_status_block"].append(raw_line)

            # Telemetry Parsing
            stats_match = RE_STATS.search(raw_line)
            if stats_match:
                try:
                    parts = stats_match.group(1).split("|")
                    for p in parts:
                        if "submitted:" in p or "Jobs:" in p:
                            val = p.split(":")[1].strip().split()[0]
                            with stats_lock:
                                stats_cache["session_jobs"] = int(
                                    val.replace(",", ""))
                except (ValueError, IndexError, KeyError):
                    pass

            kudos_match = RE_KUDOS.search(raw_line)
            if kudos_match:
                try:
                    with stats_lock:
                        stats_cache["session_kudos"] = float(
                            kudos_match.group(1).replace(",", ""))
                except (ValueError, IndexError):
                    pass

            kudos_hr_match = RE_KUDOS_HR.search(raw_line)
            if kudos_hr_match:
                try:
                    with stats_lock:
                        stats_cache["session_kudos_hr"] = float(
                            kudos_hr_match.group(1).replace(",", ""))
                except (ValueError, IndexError):
                    pass

            job_id_match = RE_JOB_ID.search(raw_line)
            if job_id_match:
                with stats_lock:
                    stats_cache["current_job_id"] = job_id_match.group(0)
                    stats_cache["activity_text"] = "Starting Inference"
                    stats_cache["activity_subtext"] = f"Job ID: {job_id_match.group(0)}"

            job_model_match = RE_JOB_MODEL.search(raw_line)
            if job_model_match:
                with stats_lock:
                    stats_cache["current_job_model"] = job_model_match.group(0)
                    stats_cache["activity_text"] = "Inference in Progress"
                    stats_cache["activity_subtext"] = f"Model: {job_model_match.group(0)}"

            job_details_match = RE_JOB_DETAILS.search(raw_line)
            if job_details_match:
                with stats_lock:
                    stats_cache["current_job_details"] = job_details_match.group(
                        0)
                    stats_cache["activity_subtext"] += f" ({job_details_match.group(0)})"

            if "Inference finished for job" in raw_line:
                with stats_lock:
                    stats_cache["activity_text"] = "System Idle"
                    stats_cache["activity_subtext"] = "Waiting for next job..."
                    stats_cache["current_job_id"] = None

            if "Total Kudos Accumulated:" in raw_line:
                try:
                    val = raw_line.split("Total Kudos Accumulated:")[1].split(
                        "(")[0].strip().replace(",", "")
                    with stats_lock:
                        stats_cache["kudos"] = float(val)
                except (ValueError, IndexError):
                    pass

            live_match = RE_LIVE_STATUS.search(raw_line)
            if live_match:
                marker = live_match.group(1)
                proc_id = live_match.group(2)

                with stats_lock:
                    procs = stats_cache.get("processes", [])
                    found = False
                    new_state = "BUSY" if marker == "+" else "IDLE" if marker == "-" else "PROCESSING"

                    for i, p in enumerate(procs):
                        if f"Process {proc_id}" in p:
                            procs[i] = re.sub(r"\(.*?\)", f"({new_state})", p)
                            found = True
                            break

                    if not found:
                        stats_cache.setdefault("processes", []).append(
                            f"Process {proc_id} ({new_state})")

            socketio.emit("stats_update", stats_cache)
            socketio.emit("log", {"line": line})

        self.worker_proc.wait()
        socketio.emit("worker_status", {"running": False})
        print("Log tailing stopped.")

    def start_worker(self, extra_args=""):
        if self.worker_proc and self.worker_proc.poll() is None:
            return False

        self.worker_start_time = time.time()

        with stats_lock:
            stats_cache["session_jobs"] = 0
            stats_cache["session_kudos"] = 0
            stats_cache["session_kudos_hr"] = 0
            stats_cache["processes"] = []
            stats_cache["activity_text"] = "Starting Worker"
            stats_cache["activity_subtext"] = "Initializing..."
            stats_cache["worker_start_time"] = self.worker_start_time
        socketio.emit("stats_update", stats_cache)

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        if os.name == 'nt':
            if os.path.exists("horde-worker-reGen/horde-bridge.cmd"):
                cmd = ["cmd", "/c", "horde-bridge.cmd"] + extra_args.split()
            else:
                cmd = [sys.executable, "-u", "run_worker.py"] + \
                    extra_args.split()
        else:
            cmd = ["bash", "./horde-bridge.sh"] + extra_args.split()

        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0

        self.worker_proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.PIPE,
            text=True, bufsize=1, universal_newlines=True, env=env,
            creationflags=creationflags, cwd="horde-worker-reGen"
        )

        try:
            self.worker_proc.stdin.write("\n")
            self.worker_proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass

        socketio.emit("worker_status", {"running": True})
        self.worker_thread = threading.Thread(
            target=self._tail_logs, daemon=True)
        self.worker_thread.start()
        return True

    def stop_worker(self, force=False):  # noqa: C901
        target_proc = self.worker_proc
        target_pid = None

        if target_proc and target_proc.poll() is None:
            target_pid = target_proc.pid
        else:
            # Fallback to finding by cmdline if self.worker_proc is lost/stale
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    cmdline = proc.info['cmdline']
                    if cmdline and any('run_worker.py' in arg for arg in cmdline):
                        target_pid = proc.info['pid']
                        # Try to get a handle on the process object for wait()
                        target_proc = psutil.Process(target_pid)
                        break
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

        if not target_pid:
            return {"status": "no_worker_found"}

        if force:
            if os.name == 'nt':
                subprocess.run(["taskkill", "/F", "/T", "/PID",
                               str(target_pid)], capture_output=True)
            else:
                os.kill(target_pid, signal.SIGKILL)
            socketio.emit("worker_status", {"running": False})
            return {"status": "stopped"}

        # Graceful Shutdown Sequence
        if os.name == 'nt':
            try:
                os.kill(target_pid, signal.CTRL_C_EVENT)
            except (OSError, PermissionError):
                pass  # Signal failed, but we still wait for potential manual or prior drain
        else:
            os.kill(target_pid, signal.SIGINT)

        # Wait for drain (e.g., worker finishing current job)
        def wait_and_cleanup():
            try:
                if hasattr(target_proc, 'wait'):
                    # subprocess.Popen or psutil.Process both have wait()
                    if isinstance(target_proc, subprocess.Popen):
                        target_proc.wait(timeout=120)
                    else:
                        target_proc.wait(timeout=120)

                # If we get here, it stopped gracefully
                socketio.emit("worker_status", {"running": False})
                print(f"Worker {target_pid} stopped gracefully.")
            except (subprocess.TimeoutExpired, psutil.TimeoutExpired):
                msg = f"Worker {target_pid} drain timeout expired. Force killing... current job may not have been submitted."
                print(msg)
                socketio.emit("log", {"line": f"\n[WARNING]: {msg}\n"})

                if os.name == 'nt':
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(target_pid)], capture_output=True)
                else:
                    os.kill(target_pid, signal.SIGKILL)
                socketio.emit("worker_status", {"running": False})

        # Run wait in background so we don't block the UI response
        threading.Thread(target=wait_and_cleanup, daemon=True).start()

        return {"status": "stopping_gracefully"}


worker_manager = WorkerManager()
