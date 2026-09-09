@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\setup_environment.ps1" %*
if errorlevel 1 (
  echo.
  echo Environment setup failed. Review the message above.
  exit /b 1
)
