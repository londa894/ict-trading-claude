@echo off
REM Refreshes the news-gate calendar from the JBlanked feed. Scheduled to run every few hours
REM so the file stays < calendarMaxAgeHours (24h) old. The Python script self-loads JBLANKED_API_KEY
REM and CALENDAR_FILE_PATH from services\api\.env, so no secret appears here.
set "SCRIPT_DIR=%~dp0"
"%SCRIPT_DIR%..\.venv\Scripts\python.exe" "%SCRIPT_DIR%fetch_calendar.py"
exit /b %ERRORLEVEL%
