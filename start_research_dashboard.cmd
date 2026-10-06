@echo off
setlocal
title Trade-log Research Dashboard
cd /d "%~dp0"
echo Starting the Research-enabled dashboard at http://127.0.0.1:8792/
echo Keep this window open while using the dashboard.
echo.
call start_dashboard.cmd -Port 8792
echo.
echo The dashboard stopped or could not start. Review the message above.
pause
