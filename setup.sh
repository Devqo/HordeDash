#!/bin/bash
echo "[i] Starting Horde Worker UI Setup..."

# 1. Check for Git
if ! command -v git &> /dev/null; then
    echo "[!] Git not found! Please install Git and try again."
    exit 1
fi

# 2. Clone/Update reGen
if [ ! -d "horde-worker-reGen" ]; then
    echo "[i] Cloning AI Horde reGen..."
    git clone https://github.com/Haidra-Org/AI-Horde-worker-reGen.git horde-worker-reGen
fi

# 3. Setup Virtual Environment
if [ ! -d "venv" ]; then
    echo "[i] Creating Python virtual environment..."
    python3 -m venv venv
fi

# 4. Install dependencies
echo "[i] Installing UI dependencies..."
source venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt

echo "[v] Setup Complete!"
echo ""
echo "[i] Launching Dashboard..."
bash start_ui.sh "$@"
