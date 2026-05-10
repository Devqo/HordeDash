import re
import subprocess
from flask import request


def is_remote_request():
    host = request.headers.get('Host', '').lower()
    if 'ngrok' in host:
        return True
    if request.headers.get('X-Forwarded-For'):
        return True
    return False


def strip_ansi_python(text):
    ansi_escape = re.compile(r'(?:\x1B[@-_][0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)


def get_gpu_info():
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total,name",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, check=True
        )
        gpus = []
        for line in result.stdout.strip().split("\n"):
            util, used, total, name = line.split(", ")
            gpus.append({
                "name": name,
                "util": int(util),
                "used": int(used),
                "total": int(total)
            })
        return gpus
    except (subprocess.SubprocessError, FileNotFoundError, ValueError):
        return []
