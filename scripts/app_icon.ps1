# Desktop icon "ClipKit": opens the app in its own window (no address bar), like a program.
$root = Split-Path $PSScriptRoot -Parent
$desk = [Environment]::GetFolderPath('Desktop')
# the icon and update task used to be called "ClipKit Nina": drop the old ones so there is only one of each
Remove-Item (Join-Path $desk "ClipKit Nina.lnk") -ErrorAction SilentlyContinue
if (Get-ScheduledTask -TaskName "ClipKit Nina update" -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName "ClipKit Nina update" -Confirm:$false
}
$lnk = Join-Path $desk "ClipKit.lnk"
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
$s.TargetPath = "powershell.exe"
$s.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$root\app\start.ps1`""
$s.WorkingDirectory = $root
$s.IconLocation = "$root\app\static\clipkit.ico"
$s.Save()
Write-Host "Desktop icon ready: $lnk"

# check for a new version every 12 hours, hidden
$task = "ClipKit update"
if (-not (Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue)) {
    $act = New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory $root `
        -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$root\scripts\auto_update.ps1`""
    $when = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Hours 12)
    $opt = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $task -Action $act -Trigger $when -Settings $opt | Out-Null
    Write-Host "Auto update every 12 hours: on"
    python "$root\scripts\cutlog.py" ping "installed" | Out-Null
}
