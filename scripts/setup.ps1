# ClipKit Team: one-time machine setup (Windows). Safe to re-run.
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -Workspace D:\ClipKit
param([Parameter(Mandatory = $true)][string]$Workspace)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent

function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}
function Ensure($cmd, $wingetId, $label) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) { return }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { throw "$label is missing and winget is not available - install $label by hand" }
    Write-Host "Installing $label..."
    winget install --id $wingetId -e --silent --accept-source-agreements --accept-package-agreements
    Refresh-Path
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { throw "$label installed but '$cmd' is not on PATH yet - open a new terminal and run setup again" }
}

Ensure python "Python.Python.3.12" "Python 3.12"
Ensure ffmpeg "Gyan.FFmpeg" "ffmpeg"
Ensure npx "OpenJS.NodeJS.LTS" "Node.js (MP4 export)"

Write-Host "Installing Python packages..."
python -m pip install -r "$root\requirements.txt"
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    # ctranslate2 (faster-whisper) needs CUDA 12 cuBLAS/cuDNN on Windows
    python -m pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"
}

python "$root\scripts\setup_workspace.py" $Workspace
python "$root\scripts\fetch_model.py"
python "$root\scripts\doctor.py"
