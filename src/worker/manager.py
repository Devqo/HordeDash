import os
import sys
import time
import signal
import threading
import subprocess
import psutil
import re
import shlex

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
RE_JOB_FINISHED = re.compile(r"Inference finished for job")
RE_TOTAL_KUDOS = re.compile(r"Total Kudos Accumulated:")


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
            cache_copy = stats_cache.copy()
        socketio.emit("stats_update", cache_copy)


class WorkerManager:
    def __init__(self):
        self.worker_proc = None
        self.worker_thread = None
        self.worker_start_time = None
        self._telemetry_handlers = [
            (RE_STATS, self._handle_stats),
            (RE_KUDOS, self._handle_kudos),
            (RE_KUDOS_HR, self._handle_kudos_hr),
            (RE_JOB_ID, self._handle_job_id),
            (RE_JOB_MODEL, self._handle_job_model),
            (RE_JOB_DETAILS, self._handle_job_details),
            (RE_JOB_FINISHED, self._handle_job_finished),
            (RE_TOTAL_KUDOS, self._handle_total_kudos),
            (RE_LIVE_STATUS, self._handle_live_status),
        ]

    def _tail_logs(self):
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

            if "^^^^" in raw_line:
                log_state["in_status_block"] = True
                log_state["current_status_block"] = []
            elif "vvvv" in raw_line:
                log_state["in_status_block"] = False
                parse_status_block(log_state["current_status_block"])
            elif log_state["in_status_block"]:
                log_state["current_status_block"].append(raw_line)

            for pattern, handler in self._telemetry_handlers:
                match = pattern.search(raw_line)
                if match:
                    handler(match, raw_line)

            with stats_lock:
                cache_copy = stats_cache.copy()
            socketio.emit("stats_update", cache_copy)
            socketio.emit("log", {"line": line})

        self.worker_proc.wait()
        socketio.emit("worker_status", {"running": False})
        print("Log tailing stopped.")

    def _handle_stats(self, match, _line):
        try:
            parts = match.group(1).split("|")
            for p in parts:
                if "submitted:" in p or "Jobs:" in p:
                    val = p.split(":")[1].strip().split()[0]
                    with stats_lock:
                        stats_cache["session_jobs"] = int(val.replace(",", ""))
        except (ValueError, IndexError, KeyError):
            pass

    def _handle_kudos(self, match, _line):
        try:
            with stats_lock:
                stats_cache["session_kudos"] = float(
                    match.group(1).replace(",", ""))
        except (ValueError, IndexError):
            pass

    def _handle_kudos_hr(self, match, _line):
        try:
            with stats_lock:
                stats_cache["session_kudos_hr"] = float(
                    match.group(1).replace(",", ""))
        except (ValueError, IndexError):
            pass

    def _handle_job_id(self, match, _line):
        with stats_lock:
            stats_cache["current_job_id"] = match.group(0)
            stats_cache["activity_text"] = "Starting Inference"
            stats_cache["activity_subtext"] = f"Job ID: {match.group(0)}"

    def _handle_job_model(self, match, _line):
        with stats_lock:
            stats_cache["current_job_model"] = match.group(0)
            stats_cache["activity_text"] = "Inference in Progress"
            stats_cache["activity_subtext"] = f"Model: {match.group(0)}"

    def _handle_job_details(self, match, _line):
        with stats_lock:
            stats_cache["current_job_details"] = match.group(0)
            stats_cache["activity_subtext"] += f" ({match.group(0)})"

    def _handle_job_finished(self, _match, _line):
        with stats_lock:
            stats_cache["activity_text"] = "System Idle"
            stats_cache["activity_subtext"] = "Waiting for next job..."
            stats_cache["current_job_id"] = None

    def _handle_total_kudos(self, _match, line):
        try:
            val = line.split("Total Kudos Accumulated:")[1].split(
                "(")[0].strip().replace(",", "")
            with stats_lock:
                stats_cache["kudos"] = float(val)
        except (ValueError, IndexError):
            pass

    def _handle_live_status(self, match, _line):
        marker = match.group(1)
        proc_id = match.group(2)

        with stats_lock:
            procs = stats_cache.get("processes", [])
            found = False
            new_state = (
                "BUSY" if marker == "+" else "IDLE" if marker == "-"
                else "PROCESSING"
            )

            for i, p in enumerate(procs):
                if f"Process {proc_id}" in p:
                    procs[i] = re.sub(r"\(.*?\)", f"({new_state})", p)
                    found = True
                    break

            if not found:
                stats_cache.setdefault("processes", []).append(
                    f"Process {proc_id} ({new_state})")

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
            cache_copy = stats_cache.copy()
        socketio.emit("stats_update", cache_copy)

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        # On Windows, shlex.split with posix=True (default) strips backslashes, breaking paths.
        is_posix = os.name != 'nt'
        extra_args_list = shlex.split(extra_args, posix=is_posix) if extra_args else []

        if os.name == 'nt':
            bridge_script = os.path.join("horde-worker-reGen", "horde-bridge.cmd")
            if os.path.exists(bridge_script):
                cmd = [os.path.abspath(bridge_script)] + extra_args_list
            else:
                cmd = [sys.executable, "-u", "run_worker.py"] + extra_args_list
        else:
            cmd = ["bash", "./horde-bridge.sh"] + extra_args_list

        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0

        self.worker_proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE, text=True, bufsize=1,
            universal_newlines=True, env=env,
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

    def stop_worker(self, force=False):
        target_proc = self.worker_proc
        target_pid = None

        if target_proc and target_proc.poll() is None:
            target_pid = target_proc.pid
        else:
            target_pid, target_proc = self._find_worker_process()

        if not target_pid:
            return {"status": "no_worker_found"}

        if force:
            return self._force_stop(target_pid)

        self._send_stop_signal(target_pid)

        # Run wait in background so we don't block the UI response
        threading.Thread(
            target=self._wait_and_force_kill,
            args=(target_proc, target_pid),
            daemon=True
        ).start()

        return {"status": "stopping_gracefully"}

    def _find_worker_process(self):
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = proc.info['cmdline']
                if cmdline and any('run_worker.py' in arg for arg in cmdline):
                    pid = proc.info['pid']
                    return pid, psutil.Process(pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return None, None

    def _force_stop(self, target_pid):
        if os.name == 'nt':
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(target_pid)],
                capture_output=True
            )
        else:
            os.kill(target_pid, signal.SIGKILL)
        socketio.emit("worker_status", {"running": False})
        return {"status": "stopped"}

    def _send_stop_signal(self, target_pid):
        if os.name == 'nt':
            try:
                os.kill(target_pid, signal.CTRL_C_EVENT)
            except (OSError, PermissionError):
                pass
        else:
            os.kill(target_pid, signal.SIGINT)

    def _wait_and_force_kill(self, target_proc, target_pid):
        try:
            if hasattr(target_proc, 'wait'):
                target_proc.wait(timeout=120)

            socketio.emit("worker_status", {"running": False})
            print(f"Worker {target_pid} stopped gracefully.")
        except (subprocess.TimeoutExpired, psutil.TimeoutExpired):
            msg = (
                f"Worker {target_pid} drain timeout expired. Force killing... "
                "current job may not have been submitted."
            )
            print(msg)
            socketio.emit("log", {"line": f"\n[WARNING]: {msg}\n"})
            self._force_stop(target_pid)


worker_manager = WorkerManager()
