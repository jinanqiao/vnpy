@echo off
setlocal
cd /d "%~dp0"

if not exist "config.json" (
  if exist "..\config.json" (
    copy "..\config.json" "config.json" >nul
    echo Copied config.json from parent folder.
  ) else (
    echo config.json not found.
    echo Run install_windows.bat first, then edit config.json.
    pause
    exit /b 1
  )
)

if not exist ".venv\Scripts\activate.bat" (
  echo Virtual environment not found.
  echo Run install_windows.bat first.
  pause
  exit /b 1
)

call ".venv\Scripts\activate.bat"
set "PYTHONPATH=%~dp0;%~dp0..;%PYTHONPATH%"
echo Starting QMT gateway...
echo Keep this window open while vn.py is using QMT.
if exist "xtquant" (
  echo Found local xtquant package under %~dp0xtquant
)
if exist "..\xtquant" (
  echo Found local xtquant package under %~dp0..\xtquant
)
python qmt_gateway.py
if errorlevel 1 (
  echo.
  echo Gateway exited with error.
  echo If Python is not found, run install_windows.bat again after installing Python.
)
pause
