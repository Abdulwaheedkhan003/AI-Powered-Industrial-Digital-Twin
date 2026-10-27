@echo off
rem Starts NEXUS. Open http://localhost:8000 in a browser. Close this window to stop it.
cd /d "%~dp0backend"
start "" http://localhost:8000
python -m uvicorn main:app --port 8000
pause
