@echo off
REM Starts the Next.js frontend bound to all interfaces (0.0.0.0) so Tailscale devices can reach it.
cd /d "%~dp0"
call npm run dev:lan
