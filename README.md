# Claude Progress Bar

A small Windows window that shows **one live bar for every task Claude Code is running in the background**, across all your sessions at once, and warns you when one goes quiet.

- Background shell commands (`run_in_background`, or commands Claude moved to the background)
- Subagents, named the same way as in Claude Code's Background tasks panel
- Foreground commands that run longer than 10 seconds
- Long jobs you register yourself (renders, ffmpeg, scripts) with a real percentage

<!-- ![Claude Progress Bar](docs/screenshot.png) -->

## What the colours mean

| State | Bar | Tray icon |
| --- | --- | --- |
| Running, output still changing | black text | green |
| No activity for 2 minutes | amber | amber |
| Stalled: quiet 5+ minutes **and** evidence it is stuck (see below) | **red + alert sound + taskbar flash** | red |
| Finished | green | |
| Failed, or the process died | dark red | |
| Closed after 1 h with no progress (no process to check) | grey | |
| Nothing running (still listening) | window hidden | grey |

Right-click a row to copy a ready-made message for Claude ("Please check whether this task is still making progress or has stalled: …") or the task's log path, or to dismiss the bar.

The window hides itself when nothing is running and comes back when a new task starts. Closing it with **X** only hides it; to quit, right-click the tray icon and choose **Exit** (it restarts with the next Claude task).

## Requirements

- **Windows 10 or 11** (macOS and Linux are not supported yet; on those the plugin installs but does nothing)
- **Claude Code** (CLI, desktop app or IDE extension)
- **Python 3** available as `python` on your PATH
- PowerShell 7 (`pwsh`) is used if installed; otherwise the built-in Windows PowerShell 5.1

## Install (as a Claude Code plugin)

In Claude Code, run:

```
/plugin marketplace add wilsonnjanjo-cloud/claude-progress-bar
/plugin install claude-progress-bar@claude-progress-bar
```

Restart Claude Code (or start a new session). The window appears the first time Claude starts a background task or a subagent.

### Manual install (without the plugin system)

1. Download this repository (green **Code** button, **Download ZIP**) and unzip it somewhere permanent, for example `C:\Tools\claude-progress-bar`.
2. Add the hooks from [`hooks/hooks.json`](hooks/hooks.json) to the `"hooks"` section of `%USERPROFILE%\.claude\settings.json`, replacing `${CLAUDE_PLUGIN_ROOT}` with the folder you unzipped to (use forward slashes, e.g. `C:/Tools/claude-progress-bar`).

### Start it at Windows logon (optional)

The hooks start the window automatically. If you also want the tray icon from the moment you log in, copy the shortcut the plugin creates into your Startup folder (run once, in PowerShell, after the window has appeared at least once):

```powershell
Copy-Item "$env:LOCALAPPDATA\ClaudeProgressBar\Claude Progress Bar.lnk" "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\"
```

## Tracking your own long jobs

`scripts/progress_job.ps1` adds a bar with a real percentage for any job that writes a progress file:

```powershell
# ffmpeg run with:  ffmpeg ... -progress C:\work\prog.txt
pwsh -File scripts\progress_job.ps1 -Name "Final render" -Kind ffmpeg -Log C:\work\prog.txt -Total 1051 -ProcessId 1234

# any script that writes "NN|text" or "NN% text" lines (e.g. "42% (80/192 frames)"),
# ending with "DONE|text", "FAIL|text" or a 100% line
pwsh -File scripts\progress_job.ps1 -Name "Export" -Kind pct -Log C:\work\export_prog.txt -ProcessId 5678
```

Running the same job again with the same log file replaces its old bar.

### How it decides a task has stalled

Silence alone never sets off the alarm. A quiet task turns amber (no sound); it turns **red with a sound only when there is evidence** that it is stuck:

| Task | Treated as healthy while quiet | Alarm |
| --- | --- | --- |
| Job registered with `-ProcessId` | the process is using CPU | the process is alive but idle for the whole quiet spell (hung) |
| Subagent | it is waiting on a tool call (a long render, a big search) | quiet after a tool result, when it should be working |
| Background command | it has never printed anything (some commands are silent) | it printed output, then stopped |
| Any job | it is within 3x its own longest normal pause | quiet past both the 5-minute limit and that learned pause |

Finished work never alarms: `DONE|…`, a 100% line, or the process ending closes the bar. A hand-registered job with no process to watch closes itself (grey) after 1 hour of silence. Each alarm is written to `hook_events.log`, so any false alarm can be traced.

For the best results, register long jobs with `-ProcessId`. If you need quiet, the tray menu has **Mute alarm sound** (stalled bars still turn red), and right-clicking a bar offers **Dismiss**.

## How it works

- `scripts/hook.py` runs on Claude Code hook events (`PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `SubagentStart`, `SubagentStop`). It writes one small JSON file per task and never blocks or slows Claude.
- `scripts/progress_window.ps1` is the window (WinForms). It checks the task files every 2 seconds and watches each task's output file or transcript for activity. One copy runs at a time.
- `scripts/launch_window.vbs` starts the window without a console flash. The hook opens it through Explorer so the window runs as a normal app, separate from Claude Code.

Runtime data (task files, an event log, the launcher shortcut) lives in `%LOCALAPPDATA%\ClaudeProgressBar`. Set the `CLAUDE_PROGRESS_DATA` environment variable to use another folder. The event log contains your project and task names and stays on your PC.

## Uninstall

```
/plugin uninstall claude-progress-bar@claude-progress-bar
```

Then quit the window from its tray icon and delete `%LOCALAPPDATA%\ClaudeProgressBar` (and the Startup shortcut, if you added one).

## Known limits

- Windows only for now. A macOS version needs a different window (a menu-bar app); see [Contributing](#contributing).
- Always-on-top is best effort: some Windows setups clear it. The tray icon and taskbar button always work.

## Contributing

Contributions are welcome: bug reports, ideas and pull requests. A macOS version would be especially welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for how to report a bug or send a change. Because this plugin runs on every command Claude Code runs, every pull request is reviewed line by line before it is merged.

## License

MIT
