@echo off
setlocal
cd /d "%~dp0"

echo [1/4] Checking Python...
set "PYTHON_CMD="
set "PYTHON_ARGS="

if defined PYTHON_EXE (
  "%PYTHON_EXE%" --version >nul 2>nul
  if not errorlevel 1 (
    set "PYTHON_CMD=%PYTHON_EXE%"
  )
)

if not defined PYTHON_CMD (
  python --version >nul 2>nul
  if not errorlevel 1 (
    set "PYTHON_CMD=python"
  )
)

if not defined PYTHON_CMD (
  py -3 --version >nul 2>nul
  if not errorlevel 1 (
    set "PYTHON_CMD=py"
    set "PYTHON_ARGS=-3"
  )
)

if not defined PYTHON_CMD (
  echo Python not found. Trying to install Python 3.11 64-bit for current user...
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_python_windows.ps1"
  if errorlevel 1 (
    echo Failed to auto install Python.
    echo Please install Python 3.8-3.11 manually and check "Add python.exe to PATH",
    echo or set PYTHON_EXE to your QMT Python path before running this script.
    echo Example:
    echo   set PYTHON_EXE=C:\Python311\python.exe
    echo   install_windows.bat
    pause
    exit /b 1
  )
)

if not defined PYTHON_CMD (
  if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
    set "PYTHON_CMD=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
  )
)

if not defined PYTHON_CMD (
  python --version >nul 2>nul
  if not errorlevel 1 (
    set "PYTHON_CMD=python"
  )
)

if not defined PYTHON_CMD (
  py -3 --version >nul 2>nul
  if not errorlevel 1 (
    set "PYTHON_CMD=py"
    set "PYTHON_ARGS=-3"
  )
)

if not defined PYTHON_CMD (
  echo Python was installed, but this cmd session still cannot find it.
  echo Please close this window, open install_windows.bat again, or run:
  echo   set PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe
  echo   install_windows.bat
  pause
  exit /b 1
)
echo Using Python: %PYTHON_CMD% %PYTHON_ARGS%
%PYTHON_CMD% %PYTHON_ARGS% --version

echo [2/4] Creating virtual environment...
if not exist ".venv" (
  %PYTHON_CMD% %PYTHON_ARGS% -m venv .venv
)

echo [3/4] Installing dependencies...
call ".venv\Scripts\activate.bat"
set "PYTHONPATH=%~dp0;%~dp0..;%PYTHONPATH%"
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 (
  echo Failed to install dependencies.
  pause
  exit /b 1
)

echo [4/4] Preparing config...
if not exist "config.json" (
  if exist "..\config.json" (
    copy "..\config.json" "config.json" >nul
    echo Copied config.json from parent folder.
  ) else (
    copy "config.example.json" "config.json" >nul
    echo Created config.json from config.example.json.
    echo Please edit config.json before starting the gateway.
  )
) else (
  echo config.json already exists, skipped.
)

echo.
echo Install finished.
echo Next: edit config.json, login QMT client, then run start_windows.bat.
pause
