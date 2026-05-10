import re
from src.worker.manager import RE_KUDOS, RE_JOB_ID, RE_LIVE_STATUS, RE_JOB_DETAILS, RE_KUDOS_HR

def test_re_kudos():
    line = "2023-10-27 10:00:00 - Total Session Kudos: 1,234.56"
    match = RE_KUDOS.search(line)
    assert match is not None
    assert match.group(1) == "1,234.56"

def test_re_kudos_hr():
    line = "2023-10-27 10:00:00 - Session: 500.25 kudos/hr"
    match = RE_KUDOS_HR.search(line)
    assert match is not None
    assert match.group(1) == "500.25"

def test_re_job_id():
    line = "Starting inference for job 00000000-0000-0000-0000-000000000000"
    match = RE_JOB_ID.search(line)
    assert match is not None
    assert match.group(0) == "00000000-0000-0000-0000-000000000000"

def test_re_job_details():
    line = "Generating 512x512 for 30 steps with sampler k_euler_a for a batch of 1"
    match = RE_JOB_DETAILS.search(line)
    assert match is not None
    assert match.group(0) == "512x512 for 30 steps with sampler k_euler_a for a batch of 1"

def test_re_live_status():
    line = "Worker 1 [ + ]:1 processing job..."
    match = RE_LIVE_STATUS.search(line)
    assert match is not None
    assert match.group(1) == "+"
    assert match.group(2) == "1"

    line2 = "Worker 2 [ - ]:2 idle..."
    match2 = RE_LIVE_STATUS.search(line2)
    assert match2 is not None
    assert match2.group(1) == "-"
    assert match2.group(2) == "2"
