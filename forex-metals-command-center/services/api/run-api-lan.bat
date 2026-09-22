@echo off
REM Starts the FastAPI backend bound to all interfaces (0.0.0.0) so Tailscale devices can reach it.
REM Runs from this script's own folder (services\api) so .env and the sqlite path resolve correctly.
cd /d "%~dp0"
REM --reload auto-applies code changes so you don't have to restart after edits.
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
