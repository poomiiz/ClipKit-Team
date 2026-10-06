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

# check for a new version every 12 hours, hidden
$task = "ClipKit Nina update"
if (-not (Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue)) {
    $act = New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory $root `
        -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$root\scripts\auto_update.ps1`""
    $when = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Hours 12)
    $opt = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $task -Action $act -Trigger $when -Settings $opt | Out-Null
    Write-Host "Auto update every 12 hours: on"
    python "$root\scripts\cutlog.py" ping "installed" | Out-Null
}
