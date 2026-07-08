<#
  ge_loadtest.ps1 <mapI3D> [watchSeconds]
  Launches GE with the map, watches editor_log for onFileOpen/Virtual Texture, then watches for a post-load
  hard crash (process exit or crash-reporter window). Writes a one-line verdict to <scratch>\ge_verdict.txt.
  Meant to be run in the background; poll the verdict file.
#>
param([Parameter(Mandatory=$true)][string]$Map, [int]$WatchSeconds = 210)
$GE  = "C:\Program Files\GIANTS Software\GIANTS_Editor_10.0.13\editor.exe"
$LOG = "$env:LOCALAPPDATA\GIANTS Editor 64bit 10.0.13\editor_log.txt"
$VERDICT = "C:\Temp\claude\C--repos-fs25-orchestrator\18d3bd49-7760-4791-891a-1b135ffaf3ad\scratchpad\ge_verdict.txt"
Set-Content $VERDICT "RUNNING $(Split-Path $Map -Leaf)"
Get-Process editor -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 3
Start-Process $GE -ArgumentList "`"$Map`""
$loaded = $false; $loadTime = $null; $verdict = "TIMEOUT (no load within ${WatchSeconds}s)"
$deadline = (Get-Date).AddSeconds($WatchSeconds)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 6
    $proc = @(Get-Process editor -ErrorAction SilentlyContinue)
    $cr = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -match "Crash Reporter" })
    $txt = Get-Content $LOG -Raw -ErrorAction SilentlyContinue
    if ($cr.Count -gt 0) { $verdict = "CRASH (reporter) loaded=$loaded"; break }
    if ($proc.Count -eq 0) { $verdict = "CRASH (exit) loaded=$loaded"; break }
    if (-not $loaded -and $txt -match "Virtual Texture initialized") { $loaded = $true; $loadTime = Get-Date }
    if ($loaded -and ((Get-Date) - $loadTime).TotalSeconds -ge 40) { $verdict = "STABLE (loaded, survived 40s post-load)"; break }
}
Set-Content $VERDICT $verdict
"VERDICT: $verdict"
