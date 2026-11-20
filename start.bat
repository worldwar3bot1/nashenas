@echo off
cd /d "%~dp0"
if not exist ".env" (
    echo ERROR: First copy .env.example to .env and set BOT_TOKEN.
    pause
    exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
    py -m venv .venv
)
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe bot.py
pause
