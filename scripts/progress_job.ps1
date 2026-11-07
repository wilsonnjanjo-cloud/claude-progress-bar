# Register a long job by hand with the Claude Progress Bar window (opens it if needed), for a real % bar.
# usage: progress_job.ps1 -Name "Render" -Kind hf -Log C:\...\render.log [-ProcessId 1234] [-StallSeconds 120] [-Project Demo]
#        progress_job.ps1 -Name "Master" -Kind ffmpeg -Log C:\...\prog.txt -Total 1051
#        progress_job.ps1 -Name "Export" -Kind pct -Log C:\...\export_prog.txt
# pct logs: write "NN|text" or "NN% text" lines and end with "DONE|text" (or "FAIL|text"); a 100% line also counts as done.
# Always pass -ProcessId when you can: the bar then knows the moment the process ends instead of waiting for silence.
# -ProcessId: the bar turns red if that process exits without reporting an end. -StallSeconds: red + alert after this much quiet (default 300; amber at 2 min).
param([Parameter(Mandatory)][string]$Name, [ValidateSet("hf", "ffmpeg", "pct", "shell")][string]$Kind = "pct",
      [Parameter(Mandatory)][string]$Log, [int]$Total = 0, [int]$ProcessId = 0, [int]$StallSeconds = 300, [string]$Project = "")
$data = if ($env:CLAUDE_PROGRESS_DATA) { $env:CLAUDE_PROGRESS_DATA } else { Join-Path $env:LOCALAPPDATA "ClaudeProgressBar" }
$dir = Join-Path $data "jobs"; New-Item -ItemType Directory -Force $dir | Out-Null
$job = @{ kind = $Kind; name = $Name; log = $Log; total = $Total; stall = $StallSeconds; started = [DateTimeOffset]::Now.ToUnixTimeMilliseconds() / 1000 }
if ($ProcessId) { $job.pid = $ProcessId }
if ($Project) { $job.project = $Project }
$key = ($Name -replace '[^A-Za-z0-9._-]+', '_') + "_" + (Get-Date -Format "HHmmss")   # start time: a re-run of the same job gets its own id
# a re-run of the same job (same log file) replaces the earlier registration instead of leaving it to read as stalled
Get-ChildItem $dir -Filter "manual_*.job" -ErrorAction SilentlyContinue | ForEach-Object {
  try { if ((Get-Content -LiteralPath $_.FullName -Raw -ErrorAction Stop | ConvertFrom-Json).log -eq $Log) { Remove-Item -LiteralPath $_.FullName -ErrorAction SilentlyContinue } } catch {}
}
$job | ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $dir "manual_$key.job")
$hb = Join-Path $data "window.heartbeat"
$fresh = (Test-Path $hb) -and (((Get-Date) - (Get-Item $hb).LastWriteTime).TotalSeconds -lt 8)
if (-not $fresh) {
  $lnk = Join-Path $data "Claude Progress Bar.lnk"   # made by hook.py on its first run
  if (Test-Path $lnk) { Start-Process explorer.exe -ArgumentList ("`"" + $lnk + "`"") }
  else { Start-Process wscript.exe -ArgumentList ("`"" + (Join-Path $PSScriptRoot "launch_window.vbs") + "`" `"" + $data + "`"") }
}
