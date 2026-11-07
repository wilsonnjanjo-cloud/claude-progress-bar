"""Claude Code hook (all sessions): registers background work with the Claude Progress Bar window (Windows only).
  PostToolUse Bash|PowerShell with run_in_background -> a 'shell' bar (runs while its output file is held open)
  SubagentStart / PostToolUse Agent / SubagentStop     -> an 'agent' bar (activity = its transcript growing)
Jobs are small JSON files in JOBS; the window (progress_window.ps1) draws them. Never blocks or fails Claude."""
import json, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))  # code folder (moves when the plugin updates)
# data folder: stable across updates; override with CLAUDE_PROGRESS_DATA
DATA = os.environ.get("CLAUDE_PROGRESS_DATA") or os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "ClaudeProgressBar")
JOBS = os.path.join(DATA, "jobs")
WINDOW = os.path.join(HERE, "progress_window.ps1")
HEARTBEAT = os.path.join(DATA, "window.heartbeat")
LAUNCHER = os.path.join(DATA, "Claude Progress Bar.lnk")


def safe(s):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s)[:80]


def write_job(key, job):
    os.makedirs(JOBS, exist_ok=True)
    path = os.path.join(JOBS, safe(key) + ".job")
    old = {}
    if os.path.exists(path):
        try:
            old = json.load(open(path, encoding="utf-8"))
        except Exception:
            pass
    old.update({k: v for k, v in job.items() if v not in (None, "")})
    tmp = path + ".tmp"
    json.dump(old, open(tmp, "w", encoding="utf-8"))
    os.replace(tmp, path)


def log_event(*parts):
    # one line per registration, so a "missing" bar can be checked against what the hook actually saw
    try:
        os.makedirs(DATA, exist_ok=True)
        path = os.path.join(DATA, "hook_events.log")
        if os.path.exists(path) and os.path.getsize(path) > 1_000_000:
            os.replace(path, path + ".old")
        with open(path, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + " | ".join(str(x) for x in parts) + chr(10))
    except Exception:
        pass


def ensure_launcher():
    # a shortcut (wscript -> launch_window.vbs -> hidden pwsh) in the data folder. Opening it through Explorer starts the
    # window as a normal app, outside Claude Code's process container, with no console flash. Rebuilt when the code
    # folder changes (plugin update). Returns the shortcut path, or "" if it could not be made.
    stamp = LAUNCHER + ".target"
    try:
        if os.path.exists(LAUNCHER) and open(stamp, encoding="utf-8").read() == HERE:
            return LAUNCHER
    except OSError:
        pass
    os.makedirs(DATA, exist_ok=True)
    vbs = os.path.join(HERE, "launch_window.vbs")
    q = lambda x: x.replace("'", "''")
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s');"
          "$s.TargetPath=Join-Path $env:WINDIR 'System32\\wscript.exe';"
          "$s.Arguments='\"%s\" \"%s\"';$s.WorkingDirectory='%s';$s.WindowStyle=7;$s.Save()"
          % (q(LAUNCHER), q(vbs), q(DATA), q(HERE)))
    try:
        subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps],
                       creationflags=0x08000000, timeout=20, capture_output=True)
        if os.path.exists(LAUNCHER):
            open(stamp, "w", encoding="utf-8").write(HERE)
            log_event("launcher created", LAUNCHER)
            return LAUNCHER
    except Exception:
        pass
    return ""


def ensure_window():
    try:
        if time.time() - os.path.getmtime(HEARTBEAT) < 8:
            return
    except OSError:
        pass
    try:  # claim the launch so two hooks firing together don't open two windows
        fd = os.open(HEARTBEAT + ".launch", os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        try:
            if time.time() - os.path.getmtime(HEARTBEAT + ".launch") < 15:
                return
            os.remove(HEARTBEAT + ".launch")
        except OSError:
            return
    lnk = ensure_launcher()
    if lnk:
        # start it through Explorer: the window then runs as a normal app, outside Claude Code's process container,
        # where Windows lets it stay always-on-top (launched straight from a hook it kept dropping behind other windows)
        try:
            subprocess.Popen(["explorer.exe", lnk], creationflags=0x08000000, close_fds=True)
            log_event("window launched", "via explorer")
            return
        except OSError:
            pass
    args = ["pwsh", "-NoProfile", "-WindowStyle", "Hidden", "-File", WINDOW, "-DataDir", DATA]
    # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP, plus CREATE_BREAKAWAY_FROM_JOB so the window outlives the hook
    # (DETACHED_PROCESS makes pwsh exit at once, so it is not used)
    try:
        subprocess.Popen(args, creationflags=0x08000000 | 0x00000200 | 0x01000000, close_fds=True)
    except OSError:
        subprocess.Popen(args, creationflags=0x08000000 | 0x00000200, close_fds=True)


FG_MIN_S = 10  # a foreground command gets a bar only once it has run this long


def spawn_delayed_launch(job_path):
    try:
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "--launch-if-running", job_path],
                         creationflags=0x08000000 | 0x00000200 | 0x01000000, close_fds=True)
    except OSError:
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "--launch-if-running", job_path],
                         creationflags=0x08000000 | 0x00000200, close_fds=True)


def launch_if_running(job_path):
    time.sleep(FG_MIN_S)
    try:
        if json.load(open(job_path, encoding="utf-8")).get("state") == "run":
            ensure_window()
    except Exception:
        pass


def reopen_if_needed():
    # the window may have been closed (X button) while work is still going: reopen it on any hook event
    try:
        if time.time() - os.path.getmtime(HEARTBEAT) < 8:
            return
    except OSError:
        pass
    now = time.time()
    for name in os.listdir(JOBS) if os.path.isdir(JOBS) else []:
        if not name.endswith(".job"):
            continue
        try:
            j = json.load(open(os.path.join(JOBS, name), encoding="utf-8"))
        except Exception:
            continue
        if j.get("kind") == "fg":
            # a foreground command still running past the 10 s threshold (and under the 11 min cap)
            if j.get("state") == "run" and FG_MIN_S < now - float(j.get("started") or now) < 660:
                break
        elif j.get("state") not in ("done", "fail"):
            break
    else:
        return
    log_event("window reopened", "jobs still running")
    ensure_window()


def main():
    d = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace") or "{}")  # hook input is UTF-8
    ev = d.get("hook_event_name")
    project = os.path.basename((d.get("cwd") or "").rstrip("\\/")) or "?"
    sess = (d.get("session_id") or "")[:8]
    tasks_dir = os.path.join(os.path.dirname(d.get("scratchpad_dir") or ""), "tasks")
    tp = d.get("transcript_path") or ""
    sub_dir = os.path.join(tp[:-6] if tp.endswith(".jsonl") else tp, "subagents")

    shell = d.get("tool_name") in ("Bash", "PowerShell")
    tuid = d.get("tool_use_id") or ""
    fg_path = os.path.join(JOBS, safe(f"{sess}_fg_{tuid[-12:]}") + ".job") if tuid else ""

    if ev == "PreToolUse" and shell and tuid:
        ti = d.get("tool_input") or {}
        if ti.get("run_in_background"):
            return  # background starts are handled at PostToolUse
        # Pre and Post hooks run at the same time; for a quick command Post can finish first. Create the file only if
        # it does not exist yet (atomic), so a late Pre can never flip a finished command back to "running".
        os.makedirs(JOBS, exist_ok=True)
        try:
            fd = os.open(fg_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            # tuid + transcript let the window see the command's result even when no Post hook fires
            # (a permission denial or an interrupted turn sends no PostToolUse)
            json.dump({"kind": "fg", "name": ti.get("description") or ti.get("command", "")[:60],
                       "project": project, "started": time.time(), "state": "run",
                       "tuid": tuid, "transcript": tp, "session": d.get("session_id") or ""}, f)
        log_event("fg start", sess, tuid[-12:], project, ti.get("description") or ti.get("command", "")[:60])
        spawn_delayed_launch(fg_path)   # the window opens only if the command is still running after FG_MIN_S
        return

    if ev in ("PostToolUse", "PostToolUseFailure") and shell and fg_path:
        # the foreground part is over: finished, failed, or moved to the background (then the shell job below takes over)
        tr = d.get("tool_response") or {}
        moved = isinstance(tr, dict) and tr.get("backgroundTaskId")
        state = "gone" if moved else ("fail" if ev == "PostToolUseFailure" else "done")
        write_job(f"{sess}_fg_{tuid[-12:]}", {"kind": "fg", "state": state, "ended": time.time()})
        log_event("fg " + state, sess, tuid[-12:])

    if ev == "PostToolUse" and shell:
        ti = d.get("tool_input") or {}
        tr = d.get("tool_response") or {}
        tid = tr.get("backgroundTaskId") if isinstance(tr, dict) else None
        if not tid:  # covers explicit run_in_background AND commands auto-moved to the background
            return
        write_job(f"{sess}_{tid}", {"kind": "shell", "name": ti.get("description") or ti.get("command", "")[:60],
                                    "project": project, "log": os.path.join(tasks_dir, tid + ".output"),
                                    "started": time.time()})
        log_event("bg shell", sess, tid, project, ti.get("description") or ti.get("command", "")[:60])
        ensure_window()

    elif ev == "SubagentStart":
        aid = d.get("agent_id")
        if not aid:
            return
        write_job(f"{sess}_{aid}", {"kind": "agent", "name": d.get("agent_type") or "agent", "project": project,
                                    "log": os.path.join(sub_dir, f"agent-{aid}.jsonl"),
                                    "started": time.time(), "state": "run"})
        log_event("agent start", sess, aid, project, d.get("agent_type"))
        ensure_window()

    elif ev == "PostToolUse" and d.get("tool_name") == "Agent":
        tr = d.get("tool_response") or {}
        aid = tr.get("agentId") if isinstance(tr, dict) else None
        desc = (d.get("tool_input") or {}).get("description")
        if aid and desc and os.path.exists(os.path.join(JOBS, safe(f"{sess}_{aid}") + ".job")):
            write_job(f"{sess}_{aid}", {"name": desc})

    elif ev == "SubagentStop":
        aid = d.get("agent_id")
        path = os.path.join(JOBS, safe(f"{sess}_{aid}") + ".job") if aid else ""
        if path and os.path.exists(path):
            write_job(f"{sess}_{aid}", {"state": "done"})
        log_event("agent stop", sess, aid, "registered" if path and os.path.exists(path) else "NOT REGISTERED")


if __name__ == "__main__":
    if os.name != "nt":
        sys.exit(0)  # the window is Windows-only for now; on macOS/Linux the hook does nothing
    try:
        if len(sys.argv) > 2 and sys.argv[1] == "--launch-if-running":
            launch_if_running(sys.argv[2])
        else:
            main()
            reopen_if_needed()
    except Exception as e:  # never break Claude; leave a trace for debugging
        try:
            os.makedirs(DATA, exist_ok=True)
            with open(os.path.join(DATA, "hook_errors.log"), "a", encoding="utf-8") as f:
                f.write(f"{time.ctime()} {e!r}\n")
        except Exception:
            pass
