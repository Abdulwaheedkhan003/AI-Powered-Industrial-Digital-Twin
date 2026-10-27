@echo off
rem Starts the dashboard in your browser. Close this window to stop it.
cd /d "%~dp0"
python -m streamlit run app.py
pause
