@echo off
REM One-click launcher: starts BOTH servers (API :8000 + web :3000) bound to 0.0.0.0 for Tailscale,
REM each in its own window so you can see logs and stop them independently (close the window or Ctrl+C).
setlocal
set "ROOT=%~dp0"
start "FMCC API (:8000)" cmd /k "%ROOT%services\api\run-api-lan.bat"
start "FMCC Web (:3000)" cmd /k "%ROOT%apps\web\run-web-lan.bat"
echo.
echo Launched:
echo   API  -> http://0.0.0.0:8000  (window "FMCC API")
echo   Web  -> http://0.0.0.0:3000  (window "FMCC Web")
echo.
echo From any device signed into your tailnet, open:
echo   http://londa-desktop:3000
echo (adjust the host if 'tailscale status' shows a different name/IP)
echo.
echo This is tailnet-only. Do NOT enable Tailscale Funnel.
endlocal
