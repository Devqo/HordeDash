import pytest
from unittest.mock import patch
from src.worker.manager import worker_manager, parse_status_block
from src.state import stats_cache, log_state, stats_lock

class MockStdout:
    def __init__(self, lines):
        self.lines = lines.copy()

    def readline(self):
        if self.lines:
            return self.lines.pop(0)
        return ""

class MockProc:
    def __init__(self, lines):
        self.stdout = MockStdout(lines)

    def poll(self):
        return 0

    def wait(self):
        pass

@pytest.fixture(autouse=True)
def reset_state():
    with stats_lock:
        stats_cache.clear()
    log_state["in_status_block"] = False
    log_state["current_status_block"] = []
    worker_manager.worker_proc = None

@patch('src.worker.manager.socketio')
def test_telemetry_parsing_realistic_log_stream(mock_socketio):
    """
    Feeds a realistic log stream into the worker manager's log tailing logic
    to ensure it properly parses kudos, jobs, and the status block without needing a real worker.
    """
    raw_logs = [
        "2026-05-10 11:40:28.382 | INFO     | *:[ i ]: - Total Session Kudos: 109.44 over 3.75 minutes | Session: 1,750.52 (extrapolated) kudos/hr\n",
        "2026-05-10 11:40:28.383 | INFO     | *:[ i ]: - Total Kudos Accumulated: 51,468.00 (all workers for kewl_guy_w_4070#484899)\n",
        "2026-05-10 11:40:28.437 | INFO     | *:[ % ]: - Process 1 moved model AlbedoBase XL 3.1 to system RAM. Loading took 10.29 seconds.\n",
        "2026-05-10 11:40:28.438 | INFO     | *:[ % ]: - Starting inference for job 2e2736f2 on process 1\n",
        "2026-05-10 11:40:28.438 | INFO     | *:[ % ]: -   Model: AlbedoBase XL 3.1\n",
        "2026-05-10 11:40:28.438 | INFO     | *:[ % ]: -   1 LoRAs\n",
        "2026-05-10 11:40:28.438 | INFO     | *:[ % ]: -   640x640 for 20 steps with sampler k_dpmpp_sde for a batch of 1\n",
        "2026-05-10 11:40:41.895 | INFO     | *:: - ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n",
        "2026-05-10 11:40:41.896 | INFO     | *:: - Process info:\n",
        "2026-05-10 11:40:41.896 | INFO     | *:: -   Process 0: (SAFETY) WAITING_FOR_JOB \n",
        "2026-05-10 11:40:41.896 | INFO     | *:: -   Process 1 (INFERENCE_STARTING) (AlbedoBase XL 3.1 stable_diffusion_xl)) [last message: 13.24 secs ago: START_INFERENCE heartbeat delta: 13.24]\n",
        "2026-05-10 11:40:41.896 | INFO     | *:: - ----------------------------------------\n",
        "2026-05-10 11:40:41.896 | INFO     | *:: - Job Info:\n",
        "2026-05-10 11:40:41.896 | INFO     | *:: -   Jobs: <2e2736f2: AlbedoBase XL 3.1>\n",
        "2026-05-10 11:40:41.896 | INFO     | *:: -   Session job info: pending start: 1 (eMPS: 12) | jobs popped: 2 | submitted: 3 | faulted: 0 | slow_jobs: 0 | process_recoveries: 1 | 0.00 seconds without jobs\n",
        "2026-05-10 11:40:41.897 | INFO     | *:: - ----------------------------------------\n",
        "2026-05-10 11:40:41.897 | INFO     | *:: - Worker Info:\n",
        "2026-05-10 11:40:41.897 | INFO     | *:: -   dreamer_name: kewl_guy_w_4070 | (v10.1.2) | horde user: kewl_guy_w_4070#484899 | num_models: 4 | custom_models: False | max_power: 50 (1280x1280) | max_threads: 1 | queue_size: 0 | safety_on_gpu: False\n",
        "2026-05-10 11:40:41.897 | INFO     | *:: -   allow_img2img: True | allow_lora: True | allow_controlnet: False | allow_sdxl_controlnet: False | allow_post_processing: True | post_process_job_overlap: False\n",
        "2026-05-10 11:40:41.897 | INFO     | *:: -   unload_models_from_vram_often: False | high_performance_mode: False | moderate_performance_mode: True | high_memory_mode: False\n",
        "2026-05-10 11:40:41.897 | INFO     | *:: - vvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvv\n",
    ]

    # Setup the mock process
    worker_manager.worker_proc = MockProc(raw_logs)

    # Run the tail logs function synchronously (it will exit when readline() returns "" and poll() is not None)
    worker_manager._tail_logs()

    # Validate that stats_cache was updated correctly from the stream
    with stats_lock:
        assert stats_cache.get("session_kudos") == 109.44
        assert stats_cache.get("session_kudos_hr") == 1750.52
        assert stats_cache.get("kudos") == 51468.00
        
        # Verify Job inference start parsing
        assert stats_cache.get("current_job_id") == "2e2736f2"
        assert stats_cache.get("current_job_model") == "AlbedoBase XL 3.1"
        assert stats_cache.get("current_job_details") == "640x640 for 20 steps with sampler k_dpmpp_sde for a batch of 1"
        assert stats_cache.get("activity_text") == "Inference in Progress"
        
        # Verify Process Status block parsing
        processes = stats_cache.get("processes", [])
        assert len(processes) == 2
        assert "Process 0: (SAFETY) WAITING_FOR_JOB" in processes[0]
        assert "Process 1 (INFERENCE_STARTING)" in processes[1]
        
        # Verify Telemetry stats parsing
        assert stats_cache.get("session_jobs") == 3  # "submitted: 3" -> 3

@patch('src.worker.manager.socketio')
def test_parse_status_block_directly(mock_socketio):
    """
    Directly tests the helper method to ensure it properly isolates process blocks.
    """
    lines = [
        "2026-05-10 11:40:41.896 | INFO     | *:: - Process info:",
        "2026-05-10 11:40:41.896 | INFO     | *:: -   Process 0: (SAFETY) WAITING_FOR_JOB ",
        "2026-05-10 11:40:41.896 | INFO     | *:: -   Process 1 (IDLE) [last message: 5 secs ago]",
        "2026-05-10 11:40:41.896 | INFO     | *:: - ----------------------------------------",
        "2026-05-10 11:40:41.896 | INFO     | *:: - Job Info:",
    ]
    
    parse_status_block(lines)
    
    with stats_lock:
        processes = stats_cache.get("processes", [])
        assert len(processes) == 2
        assert "Process 0: (SAFETY) WAITING_FOR_JOB" in processes[0]
        assert "Process 1 (IDLE) [last message: 5 secs ago]" in processes[1]
