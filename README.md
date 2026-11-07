# Claude Progress Bar

A small window for **Windows** (and **macOS, in beta**) that shows **one live bar for every task Claude Code is running in the background**, across all your sessions at once, and warns you when one goes quiet.

- Background shell commands (`run_in_background`, or commands Claude moved to the background)
- Subagents, named the same way as in Claude Code's Background tasks panel
- Foreground commands that run longer than 10 seconds
- Monitor watches (the Background tasks panel's "Stops in 24m" tasks), with a `stops in …` countdown
- **Counts, automatically:** when Claude starts something that produces countable output, its bar shows how far it has got, for example **18/24 frames** for a render or **37/120 images** for a batch conversion. Nothing to set up: see [Automatic counts](#automatic-counts)
- Progress that background commands print (`42%`, `12/48`, Blender's `Fra:193`) is shown on their bar automatically
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

Each row shows the task name on the left (shortened to fit) and, always fully visible on the right, its count, percentage, time spent and any warning, for example `18/24 frames · 75% · 12m` or `3m · STALLED 5m`. The title bar adds it all up: `Claude Progress Bar - 3 running, 1 stalled`.

Right-click a row to copy a ready-made message for Claude ("Please check whether this task is still making progress or has stalled: …") or the task's log path, or to dismiss the bar.

The window hides itself when nothing is running and comes back when a new task starts. Closing it only hides it. On Windows, quit from the tray icon (**Exit**); on macOS, from the **Progress Bar** menu at the top of the screen (**Quit**). Either way it restarts with the next Claude task.

## Requirements

- **Claude Code** (CLI, desktop app or IDE extension)
- **Windows 10 or 11**: Python 3 available as `python`. PowerShell 7 (`pwsh`) is used if installed, otherwise the built-in Windows PowerShell 5.1.
- **macOS (beta)**: Python 3 available as `python3`, with tkinter. The installer from python.org includes it; with Homebrew Python, run `brew install python-tk`. If tkinter is missing you get one notification saying so.
- Linux is not supported yet; there the plugin installs but does nothing.

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

## macOS (beta)

The macOS version is new and **has not been tested on a real Mac yet**. It is a Python window with the same bars, colours, right-click menu and stall rules as the Windows one:

- It appears near the top-right corner and stays on top. It has a Dock icon (click it to bring the window back) and a **Progress Bar** menu in the menu bar with **Mute alarm sound** and **Quit**.
- Stall alarms play a sound and show a macOS notification.
- There is no menu-bar status icon yet (the Windows tray icon's equivalent).

If you use a Mac, please run `python3 tests/window_smoke_test.py` from this repository and post the output in the [macOS testers issue](https://github.com/wilsonnjanjo-cloud/claude-progress-bar/issues/1). It takes about a minute and does not touch your real data.

## Automatic counts

When Claude starts a background command (or a foreground one that runs longer than 10 seconds), the plugin starts a small watcher for it. The watcher works out what the task is producing and puts the count on that task's own bar. It needs no registration, no settings and no instructions to Claude:

- **Where:** the task's working folder (3 levels deep), any folder named in its command, and Blender's `-o` output.
- **What:** a numbered sequence (`f_0241.png`, `page_012.jpg` ...) that is growing, or a batch of new files of one type (`*.webp`).
- **Out of how many:** a range in the task's description or command (`Render frames 241-264`, `pages 1 to 40`, Blender `-s/-e`), or a number (`Convert 120 images`). Without one, the bar shows `37 new images`.

Logs, temporary files and tool folders (`.git`, `node_modules`, caches) are ignored. Each detection is written to `hook_events.log` ("output found"). The watcher stops as soon as it has found the output, when the task ends, or after 2 hours.

## Tracking your own long jobs

`scripts/progress_job.ps1` (Windows) or `scripts/progress_job.py` (macOS, also works on Windows) adds a bar with a real percentage for any job that writes a progress file:

```powershell
# ffmpeg run with:  ffmpeg ... -progress C:\work\prog.txt
pwsh -File scripts\progress_job.ps1 -Name "Final render" -Kind ffmpeg -Log C:\work\prog.txt -Total 1051 -ProcessId 1234

# any script that writes "NN|text" or "NN% text" lines (e.g. "42% (80/192 frames)"),
# ending with "DONE|text", "FAIL|text" or a 100% line
pwsh -File scripts\progress_job.ps1 -Name "Export" -Kind pct -Log C:\work\export_prog.txt -ProcessId 5678
```

```bash
# macOS
python3 scripts/progress_job.py --name "Final render" --kind pct --log ~/work/progress.txt --pid 1234
```

**Renders and exports that write numbered files** need no progress log at all: the bar counts the files. `####` in the pattern stands for the number:

```powershell
# Windows: Blender writing f_0193.png ... f_0240.png
pwsh -File scripts\progress_job.ps1 -Name "Render frames 193-240" -Kind files -Dir C:\work\frames -Pattern "f_####.png" -First 193 -Last 240 -ProcessId 1234
```

```bash
# macOS
python3 scripts/progress_job.py --name "Render frames 193-240" --kind files --dir ~/work/frames --pattern "f_####.png" --first 193 --last 240 --pid 1234
```

The bar reads `40/48 frames` and finishes by itself when the last file appears. Use `-Unit images` (`--unit`) for anything that is not frames.

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
- **Windows:** `scripts/progress_window.ps1` is the window (WinForms). `scripts/launch_window.vbs` starts it without a console flash; the hook opens it through Explorer so it runs as a normal app, separate from Claude Code.
- **macOS:** `scripts/progress_window_mac.py` is the window (tkinter). `scripts/cpb_core.py` holds the stall rules and log parsing; every macOS system call (CPU time, open files, notifications, sound) is in `scripts/cpb_platform.py`.
- Both windows check the task files every 2 seconds, watch each task's output file or transcript for activity, and run one copy at a time.

Runtime data (task files and an event log) lives in `%LOCALAPPDATA%\ClaudeProgressBar` on Windows and `~/Library/Application Support/ClaudeProgressBar` on macOS. Set the `CLAUDE_PROGRESS_DATA` environment variable to use another folder. The event log contains your project and task names and stays on your computer.

### Tests

- `python tests/test_core.py`: the shared behaviour cases (progress formats, file counts, transcripts, stall rules) against the Python core.
- `pwsh -File tests/windows_parsers_test.ps1`: the same progress and file-count cases against the Windows window.
- `python tests/watcher_test.py`: the real hook and watcher against a fake render and a fake image batch.
- `python tests/upgrade_test.py`: a newer version replaces an older running window, never the other way round.
- `python3 tests/window_smoke_test.py`: runs the Python window for a minute against six fake tasks.
- `pwsh -File tests/windows_alarm_test.ps1`: the same six tasks against the Windows window.

The Windows window implements the stall rules in PowerShell and the macOS window in Python, so a change to the rules must keep both passing.

## Updates

Turn on auto-update once and new versions arrive by themselves: in Claude Code run `/plugin`, open **Marketplaces**, choose **claude-progress-bar** and **Enable auto-update**. Claude Code then installs new versions in the background; after `/reload-plugins` or in a new session, the next task replaces the running window with the new version on its own (it never downgrades). You can also update by hand with `/plugin update claude-progress-bar@claude-progress-bar`.

## Uninstall

```
/plugin uninstall claude-progress-bar@claude-progress-bar
```

Then quit the window (tray icon on Windows, **Progress Bar** menu on macOS) and delete its data folder: `%LOCALAPPDATA%\ClaudeProgressBar` on Windows (plus the Startup shortcut, if you added one), `~/Library/Application Support/ClaudeProgressBar` on macOS.

## Known limits

- macOS support is a beta and untested on real hardware; reports welcome. No menu-bar status icon yet. Linux is not supported.
- Always-on-top is best effort: some Windows setups clear it. The tray icon and taskbar button always work.

## Contributing

Contributions are welcome: bug reports, ideas and pull requests. Testing on macOS is especially welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for how to report a bug or send a change. Because this plugin runs on every command Claude Code runs, every pull request is reviewed line by line before it is merged.

## License

MIT
