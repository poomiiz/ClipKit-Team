# Runs every 12 hours (Windows Task Scheduler, task "ClipKit update"): takes the newest ClipKit Team
# from GitHub and installs new packages. Local edits in the way or no internet: skip, try again next time.
$root = Split-Path $PSScriptRoot -Parent
$log = Join-Path $root "update.log"
git -C $root fetch -q 2>$null
if ($LASTEXITCODE -ne 0) { Add-Content $log "$(Get-Date -Format s) skipped: no connection"; exit 0 }
if ((git -C $root rev-parse HEAD) -eq (git -C $root rev-parse "@{u}")) { exit 0 }
$changed = git -C $root diff --name-only HEAD "@{u}"
git -C $root pull -q --ff-only 2>$null
if ($LASTEXITCODE -ne 0) { Add-Content $log "$(Get-Date -Format s) skipped: files changed by hand - run clipkit repair"; exit 0 }
if ($changed -contains "requirements.txt") { python -m pip install -q -r "$root\requirements.txt" }
& "$PSScriptRoot\app_icon.ps1" | Out-Null
Add-Content $log "$(Get-Date -Format s) updated to $(git -C $root log -1 --format=%h)"
python "$root\scripts\cutlog.py" ping "updated" | Out-Null
