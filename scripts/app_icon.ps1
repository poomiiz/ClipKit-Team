# Desktop icon "ClipKit Nina": opens the app in its own window (no address bar), like a program.
$root = Split-Path $PSScriptRoot -Parent
$lnk = Join-Path ([Environment]::GetFolderPath('Desktop')) "ClipKit Nina.lnk"
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
$s.TargetPath = "powershell.exe"
$s.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$root\app\start.ps1`""
$s.WorkingDirectory = $root
$s.IconLocation = "C:\Windows\System32\imageres.dll,184"
$s.Save()
Write-Host "Desktop icon ready: $lnk"
