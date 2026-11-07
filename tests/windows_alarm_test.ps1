# Windows alarm test: runs a test copy of progress_window.ps1 (own mutex, own data folder, sound muted) against six
# fake tasks for ~50 s. Expected alarms: "agent stuck", "render hung process", "render no pid quiet".
#   pwsh -File tests\windows_alarm_test.ps1                        (PowerShell 7)
#   powershell -File tests\windows_alarm_test.ps1 -Exe powershell  (Windows PowerShell 5.1)
param([string]$Exe = "pwsh")
$T = Join-Path $env:TEMP "cpb_alarm_test"; New-Item -ItemType Directory -Force $T | Out-Null; $D = "$T\data_$Exe"
Get-CimInstance Win32_Process | ? { $_.CommandLine -like "*cpb_alarm_test*" -and $_.ProcessId -ne $PID } | % { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Remove-Item $D -Recurse -Force -ErrorAction SilentlyContinue; New-Item -ItemType Directory -Force "$D\jobs" | Out-Null
Set-Content "$D\alarm.muted" "muted"
$src = Get-Content (Join-Path $PSScriptRoot "..\scripts\progress_window.ps1") -Raw
$src = $src.Replace('Local\ClaudeProgressBar"', 'Local\ClaudeProgressBarTEST"').Replace('$form.Text = "Claude Progress Bar";', '$form.Text = "CPB TEST";')
[IO.File]::WriteAllText("$T\win.ps1", $src, (New-Object Text.UTF8Encoding $true))   # with BOM, so PowerShell 5.1 reads it as UTF-8

$old = (Get-Date).AddMinutes(-6); $start = [DateTimeOffset]::Now.AddMinutes(-8).ToUnixTimeMilliseconds() / 1000
function Job($name, $h) { $h.started = $start; $h | ConvertTo-Json | Set-Content "$D\jobs\$name.job" }
function Quiet($p) { (Get-Item $p).LastWriteTime = $old }

# 1 agent waiting on a tool (transcript ends with tool_use) -> no alarm
'{"type":"user","message":{"content":[{"type":"text","text":"go"}]}}' | Set-Content "$T\agent_wait.jsonl"
'{"type":"assistant","message":{"content":[{"type":"tool_use","id":"t1","name":"Bash"}]}}' | Add-Content "$T\agent_wait.jsonl"; Quiet "$T\agent_wait.jsonl"
Job "A1_agent_waiting" @{ kind = "agent"; name = "agent waiting on tool"; log = "$T\agent_wait.jsonl"; state = "run"; stall = 60 }
# 2 agent quiet after a tool result (should be thinking/writing) -> alarm
'{"type":"assistant","message":{"content":[{"type":"tool_use","id":"t1","name":"Bash"}]}}' | Set-Content "$T\agent_stuck.jsonl"
'{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"t1"}]}}' | Add-Content "$T\agent_stuck.jsonl"; Quiet "$T\agent_stuck.jsonl"
Job "A2_agent_stuck" @{ kind = "agent"; name = "agent stuck"; log = "$T\agent_stuck.jsonl"; state = "run"; stall = 60 }
# 3 render with a busy process -> no alarm
$busy = Start-Process pwsh -ArgumentList '-NoProfile -Command "$x=0; while($true){$x++} # cpb_alarm_test busy"' -PassThru -WindowStyle Hidden
"40% (77/192 frames)" | Set-Content "$T\busy.txt"; Quiet "$T\busy.txt"
Job "M3_busy" @{ kind = "pct"; name = "render busy CPU"; log = "$T\busy.txt"; pid = $busy.Id; stall = 30 }
# 4 render whose process is alive but idle (hung) -> alarm once the idle spell exceeds the limit
$idle = Start-Process pwsh -ArgumentList '-NoProfile -Command "Start-Sleep 600 # cpb_alarm_test idle"' -PassThru -WindowStyle Hidden
"40% (77/192 frames)" | Set-Content "$T\idle.txt"; Quiet "$T\idle.txt"
Job "M4_hung" @{ kind = "pct"; name = "render hung process"; log = "$T\idle.txt"; pid = $idle.Id; stall = 30 }
# 5 render that reached 100% then went quiet (the original bug) -> done, no alarm
"100% (192/192 frames)" | Set-Content "$T\done.txt"; Quiet "$T\done.txt"
Job "M5_finished" @{ kind = "pct"; name = "render finished"; log = "$T\done.txt"; stall = 30 }
# 6 render, no process given, quiet at 40% -> alarm (silence is the only evidence)
"40% (77/192 frames)" | Set-Content "$T\nopid.txt"; Quiet "$T\nopid.txt"
Job "M6_nopid_quiet" @{ kind = "pct"; name = "render no pid quiet"; log = "$T\nopid.txt"; stall = 30 }

$w = Start-Process $Exe -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$T\win.ps1`" -DataDir `"$D`"" -PassThru -WindowStyle Hidden -RedirectStandardError "$T\err_$Exe.txt"
Start-Sleep 50
"== $Exe  window alive: $(-not $w.HasExited)   stderr bytes: $((Get-Item "$T\err_$Exe.txt").Length)"
"alarms raised:"; Select-String -Path "$D\hook_events.log" -Pattern "stall alarm" | % { "  " + ($_.Line -replace '^.*stall alarm \| ', '') }
"jobs still open: " + ((Get-ChildItem "$D\jobs" -Filter *.job | % BaseName) -join ", ")
Stop-Process -Id $w.Id, $busy.Id, $idle.Id -Force -ErrorAction SilentlyContinue
$got = @(Select-String -Path "$D\hook_events.log" -Pattern "stall alarm" | % { $_.Line -replace '^.*stall alarm \| ', '' } | Sort-Object)
"RESULT: " + $(if (($got -join ",") -eq "agent stuck,render hung process,render no pid quiet") { "PASS" } else { "FAIL" })
