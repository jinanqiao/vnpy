$ErrorActionPreference = "Stop"

$PythonVersion = "3.11.9"
$PythonShort = "311"
$InstallDir = Join-Path $env:LOCALAPPDATA "Programs\Python\Python$PythonShort"
$PythonExe = Join-Path $InstallDir "python.exe"
$InstallerUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-amd64.exe"
$InstallerPath = Join-Path $env:TEMP "python-$PythonVersion-amd64.exe"

Write-Host "Checking existing Python..."

$existing = $null
try {
    $existing = Get-Command python -ErrorAction SilentlyContinue
} catch {
    $existing = $null
}

if ($existing) {
    Write-Host "Python already exists:" $existing.Source
    & python --version
    exit 0
}

try {
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
} catch {
    $pyLauncher = $null
}

if ($pyLauncher) {
    Write-Host "Python launcher already exists:" $pyLauncher.Source
    & py -3 --version
    exit 0
}

if (Test-Path $PythonExe) {
    Write-Host "Python already installed at $PythonExe"
    & $PythonExe --version
    exit 0
}

Write-Host "Downloading Python $PythonVersion 64-bit installer..."
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Invoke-WebRequest -Uri $InstallerUrl -OutFile $InstallerPath

Write-Host "Installing Python for current user..."
$args = @(
    "/quiet",
    "InstallAllUsers=0",
    "PrependPath=1",
    "Include_launcher=1",
    "Include_pip=1",
    "Include_test=0",
    "SimpleInstall=1",
    "TargetDir=$InstallDir"
)

$process = Start-Process -FilePath $InstallerPath -ArgumentList $args -Wait -PassThru
if ($process.ExitCode -ne 0) {
    throw "Python installer failed with exit code $($process.ExitCode)"
}

if (!(Test-Path $PythonExe)) {
    throw "Python installed but python.exe was not found at $PythonExe"
}

Write-Host "Python installed successfully:"
& $PythonExe --version

Write-Host "Python path:"
Write-Host $PythonExe
Write-Host ""
Write-Host "If current cmd cannot find python yet, close it and open a new cmd."
