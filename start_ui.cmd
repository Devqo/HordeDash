@echo off
echo [i] Initializing Horde Worker UI...
cd /d "%~dp0"
call venv\Scripts\activate.bat
python horde_dash.py %*
pause
