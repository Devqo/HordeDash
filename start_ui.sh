#!/bin/bash
echo "[i] Initializing Horde Worker UI..."
cd "$(dirname "$0")"
source venv/bin/activate
python3 horde_dash.py "$@"
