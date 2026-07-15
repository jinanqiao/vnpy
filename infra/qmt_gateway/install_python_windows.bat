@echo off
setlocal
cd /d "%~dp0"

echo Installing Python 3.11 64-bit for current user...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_python_windows.ps1"
if errorlevel 1 (
  echo Python installation failed.
  pause
  exit /b 1
)

echo.
echo Python installation finished.
echo You can now run install_windows.bat.
pause
