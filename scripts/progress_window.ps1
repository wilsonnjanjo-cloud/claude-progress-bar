# "Claude Progress Bar" window: one bar per background job, for every Claude Code session (fed by hook.py) and for
# jobs registered by hand (progress_job.ps1). Checks every 2 s, uses no GPU.
# Always-on listener: one instance (mutex), started by the hooks through Explorer (a launcher shortcut in the data
# folder) and optionally at Windows logon. It never quits on its own: it hides when nothing is running and shows itself
# when a task appears. X hides it until the next new task. A tray icon (grey idle, green running, amber quiet,
# red stalled) is always visible; click it to show the window, right-click > Exit to really quit.
# Kinds:
#   shell  : a Claude background command. Running while its output file is held open; done when released.
#   fg     : a Claude foreground command (shown only after it has run 10 s; capped at 11 min).
#   agent  : a Claude subagent. Done when the hook marks state=done; activity = its transcript growing.
#   hf     : HyperFrames render log ("NN%  Phase", "EXIT rc")
#   ffmpeg : ffmpeg -progress file (frame=, progress=end), needs total
#   pct    : last progress line: "NN|text" or "NN% text" (100% = finished), or "DONE|text" / "FAIL|text"
# Right-click a row: copy its details (for asking Claude) or its log path, or dismiss it. Tray menu: mute the alarm sound.
# Colours: black running · amber = 2 min no activity · RED + sound + taskbar flash = 5 min no activity · green done · dark red failed/died.
# Data (jobs, heartbeat, event log) lives in %LOCALAPPDATA%\ClaudeProgressBar unless CLAUDE_PROGRESS_DATA says otherwise,
# so it survives plugin updates that move the code folder.
param([string]$DataDir = "", [string]$JobsDir = "")
if (-not $DataDir) { $DataDir = if ($env:CLAUDE_PROGRESS_DATA) { $env:CLAUDE_PROGRESS_DATA } else { Join-Path $env:LOCALAPPDATA "ClaudeProgressBar" } }
if (-not $JobsDir) { $JobsDir = Join-Path $DataDir "jobs" }
$EventLog = Join-Path $DataDir "hook_events.log"
# single instance: a second launch (logon, hook fallback, progress_job) exits at once
$script:createdMutex = $false
$script:mutex = New-Object System.Threading.Mutex($true, "Local\ClaudeProgressBar", [ref]$script:createdMutex)
if (-not $script:createdMutex) { exit 0 }
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()
New-Item -ItemType Directory -Force $JobsDir | Out-Null
$Heartbeat = Join-Path $DataDir "window.heartbeat"

$form = New-Object System.Windows.Forms.Form
$form.Text = "Claude Progress Bar"; $form.Width = 560; $form.Height = 80; $form.TopMost = $true
# the feather icon in the title bar and on the taskbar button (Tk's standard icon, see assets/feather-icon-LICENSE.txt)
try { $form.Icon = New-Object System.Drawing.Icon (Join-Path $PSScriptRoot "..\assets\feather.ico") } catch {}
# a normal window (not a tool window) so it has a taskbar button and can always be found; always-on-top is best effort
$form.FormBorderStyle = "FixedSingle"; $form.MaximizeBox = $false; $form.StartPosition = "Manual"; $form.ShowInTaskbar = $true
$wa = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
$form.Location = New-Object System.Drawing.Point(($wa.Right - 580), ($wa.Bottom - 440))
$flow = New-Object System.Windows.Forms.FlowLayoutPanel
$flow.Dock = "Fill"; $flow.FlowDirection = "TopDown"; $flow.WrapContents = $false; $flow.AutoScroll = $true
$form.Controls.Add($flow)

$rows = @{}; $script:lastActive = Get-Date; $script:seen = $false
$WarnAfter = 120    # amber: no activity for 2 min
$StallAfter = 300   # red + alert: no activity for 5 min
$FgMinS = 10        # foreground commands get a bar only after running this long

Add-Type -Namespace Win -Name Flash -MemberDefinition @'
[StructLayout(LayoutKind.Sequential)] public struct FLASHWINFO { public uint cbSize; public IntPtr hwnd; public uint dwFlags; public uint uCount; public uint dwTimeout; }
[DllImport("user32.dll")] public static extern bool FlashWindowEx(ref FLASHWINFO pwfi);
[DllImport("user32.dll")] public static extern int GetWindowLong(IntPtr h, int i);
[DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint f);
public static void Go(IntPtr h) { var f = new FLASHWINFO(); f.cbSize = (uint)Marshal.SizeOf(f); f.hwnd = h; f.dwFlags = 3 | 12; f.uCount = 5; FlashWindowEx(ref f); }
'@
function New-DotIcon($color) {
  $bmp = New-Object System.Drawing.Bitmap 32, 32; $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.SmoothingMode = "AntiAlias"; $g.Clear([System.Drawing.Color]::Transparent)
  $g.FillEllipse((New-Object System.Drawing.SolidBrush $color), 2, 2, 28, 28)
  $g.DrawEllipse((New-Object System.Drawing.Pen ([System.Drawing.Color]::FromArgb(27, 27, 58)), 3), 3, 3, 26, 26); $g.Dispose()
  return [System.Drawing.Icon]::FromHandle($bmp.GetHicon())
}
$icons = @{ idle = New-DotIcon ([System.Drawing.Color]::Silver); run = New-DotIcon ([System.Drawing.Color]::FromArgb(16, 124, 16))
            amber = New-DotIcon ([System.Drawing.Color]::DarkOrange); red = New-DotIcon ([System.Drawing.Color]::Red) }
$tray = New-Object System.Windows.Forms.NotifyIcon
$tray.Icon = $icons.idle; $tray.Text = "Claude Progress Bar: idle (listening)"; $tray.Visible = $true
$trayMenu = New-Object System.Windows.Forms.ContextMenuStrip
$tmShow = $trayMenu.Items.Add("Show Claude Progress Bar")
$tmMute = $trayMenu.Items.Add("Mute alarm sound")
$tmExit = $trayMenu.Items.Add("Exit (restarts with the next Claude task or logon)")
# mute is remembered across restarts (a small flag file in the data folder); a stalled bar still turns red
$MuteFlag = Join-Path $DataDir "alarm.muted"
$tmMute.Checked = Test-Path $MuteFlag
$tray.ContextMenuStrip = $trayMenu
$script:userHidden = $false; $script:trayState = "idle"
function Show-Bar {
  $script:userHidden = $false
  if (-not $form.Visible) { $form.Show() }
  if ($form.WindowState -eq "Minimized") { $form.WindowState = "Normal" }
  $form.TopMost = $false; $form.TopMost = $true   # best effort: Windows on this PC sometimes refuses always-on-top
}
$tray.Add_MouseClick({ if ($_.Button -eq "Left") { Show-Bar; $form.Activate() } })
$tmShow.Add_Click({ Show-Bar; $form.Activate() })
$tmMute.Add_Click({
  $tmMute.Checked = -not $tmMute.Checked
  if ($tmMute.Checked) { Set-Content $MuteFlag "muted" } else { Remove-Item $MuteFlag -ErrorAction SilentlyContinue }
})
$tmExit.Add_Click({ $script:reallyExit = $true; $timer.Stop(); $tray.Visible = $false; $form.Close(); [System.Windows.Forms.Application]::Exit() })

function Send-Alert($what) {
  # alert sound + tray balloon + flash the taskbar button until it is looked at; bring the window back
  if (-not $tmMute.Checked) { [System.Media.SystemSounds]::Exclamation.Play() }
  try { Add-Content $EventLog ("{0} stall alarm | {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $what) } catch {}
  try { $tray.ShowBalloonTip(8000, "Claude task stalled", [string]$what, "Warning") } catch {}
  Show-Bar
  try { [Win.Flash]::Go($form.Handle) } catch {}
}

function Read-Tail($path, $bytes = 8192) {
  if (-not $path -or -not (Test-Path -LiteralPath $path)) { return "" }
  try {
    $fs = [IO.File]::Open($path, 'Open', 'Read', 'ReadWrite,Delete')
    $n = [Math]::Min($bytes, $fs.Length); $fs.Seek(-$n, 'End') | Out-Null
    $buf = New-Object byte[] $n; $fs.Read($buf, 0, $n) | Out-Null; $fs.Close()
    return [Text.Encoding]::UTF8.GetString($buf)
  } catch { return "" }
}

function Test-Held($path, $started = 0) {
  # $true while another process has the file open for writing (a running background command)
  if (-not (Test-Path -LiteralPath $path)) {
    # not created yet counts as running for 60 s; after that the file was cleaned up, so the task is over
    return (([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - [double]$started) -lt 60)
  }
  try { $s = [IO.File]::Open($path, 'Open', 'Read', 'Read'); $s.Close(); return $false } catch { return $true }
}

$script:agentLogs = @{}; $script:agentLookTime = @{}
function Resolve-AgentLog($key, $job) {
  # a session can be moved to another project folder after it starts; the hook then reports the old transcript path.
  # Find the agent's transcript by session + agent id instead (cached; retried every 30 s while missing).
  if ($job.log -and (Test-Path -LiteralPath $job.log)) { return $job.log }
  if ($script:agentLogs[$key]) { return $script:agentLogs[$key] }
  if ($script:agentLookTime[$key] -and ((Get-Date) - $script:agentLookTime[$key]).TotalSeconds -lt 30) { return $job.log }
  $script:agentLookTime[$key] = Get-Date
  $sess, $aid = $key -split '_', 2
  $hit = Get-ChildItem -Path "$env:USERPROFILE\.claude\projects\*\$sess*\subagents\agent-$aid.jsonl" -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($hit) { $script:agentLogs[$key] = $hit.FullName; return $hit.FullName }
  return $job.log
}

$script:transcripts = @{}
function Test-FgFinished($job) {
  # a foreground command is over once its result is in the session transcript. This catches commands that never send a
  # Post hook: blocked by a permission check, or cut off when a turn is interrupted.
  if (-not $job.tuid) { return $false }
  $tp = $job.transcript
  if (-not $tp -or -not (Test-Path -LiteralPath $tp)) {
    # the session may have moved to another project folder: find its transcript by session id (cached)
    if ($job.session -and -not $script:transcripts[$job.session]) {
      $hit = Get-ChildItem -Path "$env:USERPROFILE\.claude\projects\*\$($job.session).jsonl" -ErrorAction SilentlyContinue | Select-Object -First 1
      if ($hit) { $script:transcripts[$job.session] = $hit.FullName }
    }
    $tp = $script:transcripts[$job.session]
    if (-not $tp) { return $false }
  }
  return (Read-Tail $tp 400000).Contains('"tool_use_id":"' + $job.tuid + '"')
}

function Test-AgentWaiting($log) {
  # true when the agent's transcript ends on a tool call that has no result yet (it is waiting on that tool, not stuck)
  if (-not $log -or -not (Test-Path -LiteralPath $log)) { return $false }
  $lines = @((Read-Tail $log 262144) -split "`r?`n")
  for ($i = $lines.Count - 1; $i -ge 0; $i--) {
    $l = $lines[$i]; if (-not $l.Trim()) { continue }
    try { $o = $l | ConvertFrom-Json -ErrorAction Stop } catch { continue }   # the first line of a tail can be cut off
    if ($o.type -eq "assistant") { return [bool](@($o.message.content) | Where-Object { $_.type -eq "tool_use" }) }
    if ($o.type -eq "user") { return $false }
  }
  return $false
}

function Fmt-Age($sec) { if ($sec -lt 90) { "{0:0}s" -f $sec } elseif ($sec -lt 5400) { "{0:0}m" -f ($sec / 60) } else { "{0:0.0}h" -f ($sec / 3600) } }

function Get-State($job, $file) {
  # returns @(pct, text, state); pct -1 = no percentage (marquee); state = run | done | fail
  switch ($job.kind) {
    "shell" {
      if (Test-Held $job.log $job.started) { return @(-1, "running", "run") } else { return @(100, "finished", "done") }
    }
    "agent" {
      if ($job.state -eq "done") { return @(100, "finished", "done") }
      return @(-1, "working", "run")
    }
    "fg" {
      if ($job.state -eq "done") { return @(100, "finished", "done") }
      if ($job.state -eq "fail") { return @(100, "failed", "fail") }
      return @(-1, "running (foreground)", "run")
    }
    "hf" {
      $t = Read-Tail $job.log
      $m = [regex]::Match($t, 'EXIT (-?\d+)')
      if ($m.Success) { if ($m.Groups[1].Value -eq "0") { return @(100, "done", "done") } else { return @(100, "failed (exit $($m.Groups[1].Value))", "fail") } }
      $all = [regex]::Matches($t, '(\d{1,3})%\s+([A-Za-z][A-Za-z ]{2,40})')
      if ($all.Count) { $l = $all[$all.Count - 1]; return @([int]$l.Groups[1].Value, $l.Groups[2].Value.Trim().ToLower(), "run") }
      return @(0, "starting", "run")
    }
    "ffmpeg" {
      $t = Read-Tail $job.log
      if ($t -match 'progress=end') { return @(100, "done", "done") }
      $all = [regex]::Matches($t, 'frame=(\d+)')
      if ($all.Count) { $f = [int]$all[$all.Count - 1].Groups[1].Value; return @([Math]::Min(99, [int](100 * $f / [Math]::Max(1, $job.total))), "frame $f / $($job.total)", "run") }
      return @(0, "starting", "run")
    }
    default {
      # pct: the last line that carries progress, in either form:
      #   "NN|text", "DONE|text", "FAIL|text"   (the documented form)
      #   "NN% text" or "NN.N% (12/192 frames)" (what most renderers and scripts print)
      # 100% counts as finished, so a job that just stops writing at 100% never reads as stalled.
      $t = Read-Tail $job.log
      $lines = @($t -split "`r?`n" | Where-Object { $_.Trim() })
      for ($i = $lines.Count - 1; $i -ge 0; $i--) {
        $l = $lines[$i].Trim()
        if ($l -match '^(DONE|FAIL)\s*\|(.*)$') { return @(100, $Matches[2].Trim(), $(if ($Matches[1] -eq "DONE") { "done" } else { "fail" })) }
        $m = [regex]::Match($l, '^\s*(\d{1,3}(?:\.\d+)?)\s*(?:\||%)\s*(.*)$')
        if ($m.Success) {
          $p = [int][Math]::Floor([double]$m.Groups[1].Value)
          $txt = $m.Groups[2].Value.Trim(" |-()".ToCharArray()); if (-not $txt) { $txt = "running" }
          if ($p -ge 100) { return @(100, $(if ($txt -eq "running") { "finished" } else { $txt }), "done") }
          return @([Math]::Max(0, $p), $txt, "run")
        }
      }
      return @(0, "starting", "run")
    }
  }
}

function Get-Details($key) {
  # text for the right-click "Copy details": just the task (with its project) and the question for Claude
  if (-not $key) { return "" }; $r = $rows[$key]; if (-not $r) { return "" }
  return "Please check whether this task is still making progress or has stalled: $($r.title) (id $key)"
}

$menu = New-Object System.Windows.Forms.ContextMenuStrip
$miCopy = $menu.Items.Add("Copy details (to ask Claude)")
$miPath = $menu.Items.Add("Copy log path")
[void]$menu.Items.Add("-")
$miDismiss = $menu.Items.Add("Dismiss this bar (task is finished or not needed)")
function Set-Clip($text, $what) {
  # copy with retries (the clipboard can be briefly locked by another app), then confirm in the title bar
  try { [System.Windows.Forms.Clipboard]::SetDataObject([string]$text, $true, 10, 100); $form.Text = "Claude Progress Bar  -  copied: $what" }
  catch { $form.Text = "Claude Progress Bar  -  copy failed, try again" }
  $script:titleReset = (Get-Date).AddSeconds(4)
}
$menu.Add_Opening({
  # only the row that was right-clicked: its panel carries the job key in .Tag
  $c = $menu.SourceControl; while ($c -and -not ($c.Tag -is [string])) { $c = $c.Parent }
  $script:menuKey = if ($c) { $c.Tag } else { $null }
  if (-not $script:menuKey -or -not $rows[$script:menuKey]) { $_.Cancel = $true; return }
  $r = $rows[$script:menuKey]; $n = $r.title; if ($n.Length -gt 50) { $n = $n.Substring(0, 50) + "..." }
  $miCopy.Text = "Copy task for Claude:  $n"
  $miPath.Enabled = [bool]$r.job.log
  $r.panel.BackColor = [System.Drawing.Color]::LightSteelBlue   # show which row the menu is for
})
$menu.Add_Closed({ foreach ($x in $rows.Values) { $x.panel.BackColor = [System.Drawing.SystemColors]::Control } })
$miCopy.Add_Click({ $t = Get-Details $script:menuKey; if ($t) { Set-Clip $t $rows[$script:menuKey].title } })
$miPath.Add_Click({ if (-not $script:menuKey) { return }; $l = $rows[$script:menuKey].job.log; if ($l) { Set-Clip $l "log path" } })
$miDismiss.Add_Click({
  # remove one bar by hand: deletes its task file so it does not come back
  $k = $script:menuKey; if (-not $k -or -not $rows[$k]) { return }
  Remove-Item -LiteralPath (Join-Path $JobsDir "$k.job") -ErrorAction SilentlyContinue
  $flow.Controls.Remove($rows[$k].panel); $rows.Remove($k)
  try { Add-Content $EventLog ("{0} bar dismissed by user | {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $k) } catch {}
})

function New-Row($name) {
  $p = New-Object System.Windows.Forms.Panel; $p.Width = 530; $p.Height = 44; $p.Tag = [string]$name
  $lab = New-Object System.Windows.Forms.Label; $lab.Left = 4; $lab.Top = 2; $lab.Width = 520; $lab.Height = 24; $lab.Font = New-Object System.Drawing.Font("Segoe UI", 10.5)
  $lab.AutoEllipsis = $true
  $bar = New-Object System.Windows.Forms.ProgressBar; $bar.Left = 4; $bar.Top = 27; $bar.Width = 520; $bar.Height = 12; $bar.Style = "Continuous"
  $p.ContextMenuStrip = $menu; $lab.ContextMenuStrip = $menu; $bar.ContextMenuStrip = $menu   # right-click: copy details
  $p.Controls.Add($lab); $p.Controls.Add($bar); $flow.Controls.Add($p)
  return @{ panel = $p; label = $lab; bar = $bar; ended = $null; name = $name }
}

$timer = New-Object System.Windows.Forms.Timer; $timer.Interval = 2000
$timer.Add_Tick({
  try { [IO.File]::WriteAllText($Heartbeat, (Get-Date -Format o)) } catch {}
  # best effort: keep it on top while shown (Windows on this PC sometimes refuses; the tray icon and taskbar button remain)
  try { if ($form.Visible -and -not ([Win.Flash]::GetWindowLong($form.Handle, -20) -band 8)) { [void][Win.Flash]::SetWindowPos($form.Handle, [IntPtr](-1), 0, 0, 0, 0, 0x13) } } catch {}
  foreach ($f in Get-ChildItem $JobsDir -Filter *.job -ErrorAction SilentlyContinue) {
    $key = $f.BaseName
    try { $job = Get-Content -LiteralPath $f.FullName -Raw -ErrorAction Stop | ConvertFrom-Json } catch { continue }
    if ($job.kind -eq "fg") {
      # foreground command: no bar until it has run $FgMinS; quick ones never show. "gone" = moved to the background.
      $fs = if ($job.started) { [DateTimeOffset]::FromUnixTimeMilliseconds([long]([double]$job.started * 1000)).LocalDateTime } else { $f.CreationTime }
      $fileAge = ((Get-Date) - $f.LastWriteTime).TotalSeconds
      if ($job.state -eq "gone" -or ($job.state -ne "run" -and -not $rows.ContainsKey($key))) {
        if ($rows.ContainsKey($key)) { $flow.Controls.Remove($rows[$key].panel); $rows.Remove($key) }
        if ($fileAge -gt 30) { Remove-Item -LiteralPath $f.FullName -ErrorAction SilentlyContinue }  # kept briefly so a late start event can't revive it
        continue
      }
      if (-not $rows.ContainsKey($key) -and ((Get-Date) - $fs).TotalSeconds -lt $FgMinS) { continue }
      if ($job.state -eq "run" -and (Test-FgFinished $job)) {
        # finished without a Post hook (denied or interrupted): close it quietly, no bar
        if ($rows.ContainsKey($key)) { $flow.Controls.Remove($rows[$key].panel); $rows.Remove($key) }
        Remove-Item -LiteralPath $f.FullName -ErrorAction SilentlyContinue
        continue
      }
    }
    if (-not $rows.ContainsKey($key)) { $rows[$key] = New-Row $key; $script:seen = $true; Show-Bar }   # a new task shows the window (also after X)
    $r = $rows[$key]
    if ($r.ended) { continue }
    if ($job.kind -eq "agent") { $job.log = Resolve-AgentLog $key $job }
    if ($job.kind -eq "agent" -and -not $r.desc -and $job.log) {
      # Claude Code writes agent-<id>.meta.json next to the transcript, with the description shown in its Background tasks panel
      try { $r.desc = (Get-Content -LiteralPath ($job.log -replace '\.jsonl$', '.meta.json') -Raw -ErrorAction Stop | ConvertFrom-Json).description } catch {}
    }
    $title = if ($r.desc) { $r.desc } elseif ($job.name) { $job.name } else { $key }
    if ($job.project) { $title = "[$($job.project)] $title" }
    $r.title = $title
    $pct, $text, $state = Get-State $job $f
    $now = Get-Date
    $started = if ($job.started) { [DateTimeOffset]::FromUnixTimeMilliseconds([long]([double]$job.started * 1000)).LocalDateTime } else { $f.CreationTime }
    $elapsed = ($now - $started).TotalSeconds
    $r.job = $job; $r.startedAt = $started   # kept for "Copy details" (the job file is deleted when the job ends)
    $logTime = if ($job.log -and (Test-Path -LiteralPath $job.log)) { (Get-Item -LiteralPath $job.log).LastWriteTime } else { $started }
    $quiet = ($now - $logTime).TotalSeconds
    # learn the job's own rhythm: the longest normal gap between log updates seen so far
    if ($r.lastLogTime -and $logTime -gt $r.lastLogTime) {
      $gap = ($logTime - $r.lastLogTime).TotalSeconds
      if ($gap -gt [double]$r.maxGap) { $r.maxGap = $gap }
      $r.wrote = $true
    }
    if ($logTime -gt $started.AddSeconds(1)) { $r.wrote = $true }
    $r.lastLogTime = $logTime
    # Silence alone is never an alarm. Silence turns amber (no sound); red + sound needs evidence of a real stall:
    #   process given (-ProcessId): alive but no CPU use for the whole quiet spell = hung. Busy CPU = working.
    #   agent: quiet while waiting on a tool call (a long render, a big search) is normal, not a stall.
    #   background shell: a command that never prints anything is just quiet; one that printed and then stopped is suspect.
    #   anything else: quiet for longer than both the stall limit and 3x its own longest normal gap.
    $red = if ($job.stall) { [Math]::Min([double]$job.stall, $StallAfter) } else { $StallAfter }
    $red = [Math]::Max($red, 3 * [double]$r.maxGap)
    $amber = [Math]::Min($WarnAfter, $red / 2)
    $color = [System.Drawing.Color]::Black
    $extra = ""
    if ($state -eq "run") {
      $proc = if ($job.pid) { Get-Process -Id $job.pid -ErrorAction SilentlyContinue } else { $null }
      $why = $null   # a reason the silence is healthy
      if ($proc) {
        $cpu = try { $proc.TotalProcessorTime.TotalSeconds } catch { $null }
        if ($null -ne $cpu) {
          if ($null -eq $r.cpuPrev -or $cpu -gt [double]$r.cpuPrev + 0.05) { $r.cpuBusyAt = $now }
          $r.cpuPrev = $cpu
          if ($r.cpuBusyAt -and ($now - $r.cpuBusyAt).TotalSeconds -lt $red) { $why = "process busy" }
        }
      }
      if ($job.kind -eq "agent" -and $quiet -gt $amber -and (Test-AgentWaiting $job.log)) { $why = "waiting on a tool" }
      if ($job.kind -eq "shell" -and -not $r.wrote) { $why = "running, no output" }
      if ($job.pid -and -not $proc) {
        $pct, $text, $state = Get-State $job $f
        if ($state -eq "run") { $state = "fail"; $text = "process ended without finishing" }
      } elseif ($job.kind -eq "fg" -and $elapsed -gt 660) {
        $state = "fail"; $text = "no result after 11m - session closed or command interrupted"
      } elseif ($job.kind -eq "agent" -and $quiet -gt 3600) {
        $state = "fail"; $text = "no activity for 1h - session closed or agent stuck"
      } elseif ($job.kind -in "pct", "hf", "ffmpeg" -and -not $job.pid -and $quiet -gt 3600) {
        # a hand-registered job with no process to watch: after an hour of silence, close it quietly
        $state = "closed"; $text = "closed: no progress for 1h"
      } elseif ($quiet -gt $amber -and $why) {
        $extra = "   ·  $why, quiet $(Fmt-Age $quiet)"; $r.alerted = $false
      } elseif ($quiet -gt $red) {
        $extra = "   ·  STALLED [no activity for $(Fmt-Age $quiet)]"; $color = [System.Drawing.Color]::Red
        if (-not $r.alerted) { $r.alerted = $true; Send-Alert $title }
      } elseif ($quiet -gt $amber) {
        $extra = "   ·  [no activity for $(Fmt-Age $quiet)]"; $color = [System.Drawing.Color]::DarkOrange
        $r.alerted = $false
      } else { $r.alerted = $false }
    }
    if ($pct -lt 0) {
      if ($r.bar.Style -ne "Marquee") { $r.bar.Style = "Marquee"; $r.bar.MarqueeAnimationSpeed = 40 }
      $r.label.Text = "{0}   ·  {1}  ·  {2}{3}" -f $title, $text, (Fmt-Age $elapsed), $extra
    } else {
      if ($r.bar.Style -ne "Continuous") { $r.bar.Style = "Continuous" }
      $r.bar.Value = [Math]::Max(0, [Math]::Min(100, $pct))
      $r.label.Text = "{0}   {1}%  ·  {2}  ·  {3}{4}" -f $title, $pct, $text, (Fmt-Age $elapsed), $extra
    }
    $r.label.ForeColor = $color
    if ($state -ne "run") {
      $r.ended = $now
      if ($r.bar.Style -ne "Continuous") { $r.bar.Style = "Continuous" }
      $r.bar.Value = 100
      $r.label.Text = "{0}   ·  {1}  ·  {2}" -f $title, $text, (Fmt-Age $elapsed)
      $r.label.ForeColor = if ($state -eq "done") { [System.Drawing.Color]::DarkGreen } elseif ($state -eq "closed") { [System.Drawing.Color]::Gray } else { [System.Drawing.Color]::DarkRed }
      Remove-Item -LiteralPath $f.FullName -ErrorAction SilentlyContinue
    }
  }
  if ($script:titleReset -and (Get-Date) -gt $script:titleReset) { $script:titleReset = $null }
  foreach ($k in @($rows.Keys)) {
    $r = $rows[$k]
    if ($r.ended -and ((Get-Date) - $r.ended).TotalSeconds -gt 5) { $flow.Controls.Remove($r.panel); $rows.Remove($k) }
  }
  # grow with the rows up to the screen height (scrolling only beyond that), and keep the whole window on screen
  $wa = [System.Windows.Forms.Screen]::FromControl($form).WorkingArea
  $maxRows = [Math]::Max(3, [Math]::Floor(($wa.Height - 70) / 46))
  $h = 56 + 46 * [Math]::Min($maxRows, [Math]::Max(1, $rows.Count))
  if ($form.Height -ne $h) { $form.Height = $h }
  if ($form.Bottom -gt $wa.Bottom) { $form.Top = [Math]::Max($wa.Top, $wa.Bottom - $form.Height) }
  # tray icon = worst state of the running rows
  $live = @($rows.Values | Where-Object { -not $_.ended })
  $nRed = @($live | Where-Object { $_.label.ForeColor -eq [System.Drawing.Color]::Red }).Count
  $nAmber = @($live | Where-Object { $_.label.ForeColor -eq [System.Drawing.Color]::DarkOrange }).Count
  $st = if ($nRed) { "red" } elseif ($nAmber) { "amber" } elseif ($live.Count) { "run" } else { "idle" }
  if ($st -ne $script:trayState) { $tray.Icon = $icons[$st]; $script:trayState = $st }
  $tip = if ($live.Count) { "Claude Progress Bar: $($live.Count) running" + $(if ($nRed) { ", $nRed stalled" } elseif ($nAmber) { ", $nAmber quiet" } else { "" }) } else { "Claude Progress Bar: idle (listening)" }
  if ($tip.Length -gt 63) { $tip = $tip.Substring(0, 63) }
  $tray.Text = $tip
  if (-not $script:titleReset) {
    $status = if ($live.Count) { "$($live.Count) running" + $(if ($nRed) { ", $nRed stalled" } elseif ($nAmber) { ", $nAmber quiet" } else { "" }) } else { "idle" }
    $t = "Claude Progress Bar  -  $status"
    if ($form.Text -ne $t) { $form.Text = $t }
  }
  # nothing running for 6 s: hide but keep listening; it never quits on its own
  if ($rows.Count -gt 0) { $script:lastActive = Get-Date }
  elseif ($form.Visible -and ((Get-Date) - $script:lastActive).TotalSeconds -gt 6) { $form.Hide() }
})
$form.Add_FormClosing({
  # X only hides the window: it keeps listening and comes back for the next new task. Real exit = tray menu or Windows.
  if ($_.CloseReason -in @("UserClosing", "TaskManagerClosing") -and -not $script:reallyExit) {
    $_.Cancel = $true; $form.Hide(); $script:userHidden = $true
    try { Add-Content $EventLog ("{0} window hidden with X | still listening | {1} rows" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $rows.Count) } catch {}
    return
  }
  try { Add-Content $EventLog ("{0} listener exited | {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $_.CloseReason) } catch {}
})
$form.Add_FormClosed({ $tray.Visible = $false; Remove-Item $Heartbeat, "$Heartbeat.launch" -ErrorAction SilentlyContinue; [System.Windows.Forms.Application]::Exit() })
$timer.Start()
try { [IO.File]::WriteAllText($Heartbeat, (Get-Date -Format o)) } catch {}
Remove-Item "$Heartbeat.launch" -ErrorAction SilentlyContinue
try { Add-Content $EventLog ("{0} listener started | pid {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $PID) } catch {}
# which code this window runs, so a newer installed version can replace it (hook.py upgrade_window)
try { @{ pid = $PID; dir = $PSScriptRoot } | ConvertTo-Json -Compress | Set-Content -Encoding utf8 (Join-Path $DataDir "window.code") } catch {}
# message loop without a main form: the window shows and hides itself; the process lives until Exit or logoff
[System.Windows.Forms.Application]::Run()
$tray.Dispose()
