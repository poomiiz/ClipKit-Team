# Start the Video -> CapCut tool hidden on 8770 and open the page.
# Already running = only open the page. Logs: app\video_editor.log / .out.log
$app = $PSScriptRoot
# the team edition runs on its own port, so it never opens the other ClipKit on the same machine
$port = if ((git -C "$app\.." remote get-url origin 2>$null) -like "*ClipKit-Team*") { 8771 } else { 8770 }
$env:VIDEO_EDITOR_PORT = "$port"
$url = "http://127.0.0.1:$port/video-editor.html"
$up = $false
try { $up = (Invoke-WebRequest -UseBasicParsing $url -TimeoutSec 2).StatusCode -eq 200 } catch {}
if (-not $up) {
    $py = (Get-Command python -ErrorAction Stop).Source
    Start-Process -FilePath $py -ArgumentList "app.py" -WorkingDirectory $app -WindowStyle Hidden `
        -RedirectStandardError "$app\video_editor.log" -RedirectStandardOutput "$app\video_editor.out.log"
    for ($i = 0; $i -lt 20 -and -not $up; $i++) {
        Start-Sleep -Milliseconds 500
        try { $up = (Invoke-WebRequest -UseBasicParsing $url -TimeoutSec 2).StatusCode -eq 200 } catch {}
    }
}
# open as its own app window (no address bar or tabs) so it feels like a program, not a web page;
# Chrome first, else Edge (every Windows 10/11 has it), else a normal browser tab
$win = @("$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
         "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
         "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe",
         "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
         "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
$opened = $false
if ($win) { try { Start-Process $win -ArgumentList "--app=$url", "--window-size=1320,860" -ErrorAction Stop; $opened = $true } catch {} }
if (-not $opened) { Start-Process $url }
