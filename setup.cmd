@echo off
setlocal

echo [i] Starting Horde Worker UI Setup...

:: 1. Check for Git
where git >nul 2>nul
if errorlevel 1 (
    echo [!] Git not found! Please install Git and try again.
    pause
    exit /b 1
)

:: 2. Clone/Update reGen
if not exist "horde-worker-reGen" (
    echo [i] Cloning AI Horde reGen...
    git clone https://github.com/Haidra-Org/horde-worker-reGen.git horde-worker-reGen
) else (
    echo [i] horde-worker-reGen already exists.
)

:: 3. Setup Virtual Environment
if not exist "venv" (
    echo [i] Creating Python virtual environment...
    python -m venv venv
)

:: 4. Install dependencies
echo [i] Installing UI dependencies...
call venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo [v] Setup Complete!
echo.
echo [i] Launching Dashboard...
call start_ui.cmd
