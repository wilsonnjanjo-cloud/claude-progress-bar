"""Upgrade test: a fake older (or newer) window is running; the real hook must replace an older one and leave a
newer one alone, with or without the window.code record (windows before 1.3.1 did not write it).
Run: python tests/upgrade_test.py   (Windows or macOS; uses its own data folder)"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = os.path.join(tempfile.gettempdir(), "cpb_upgrade_test")
NAME = "progress_window.ps1" if sys.platform == "win32" else "progress_window_mac.py"


def run(case, version, record):
    shutil.rmtree(T, ignore_errors=True)
    data = os.path.join(T, "data")
    os.makedirs(os.path.join(data, "jobs"))
    old = os.path.join(T, "v" + version.replace(".", "_"), "scripts")
    os.makedirs(old)
    os.makedirs(os.path.join(old, "..", ".claude-plugin"))
    json.dump({"version": version}, open(os.path.join(old, "..", ".claude-plugin", "plugin.json"), "w"))
    # the fake window: a sleeping process whose command line names <old>/scripts/<window script>
    fake = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)", os.path.join(old, NAME)])
    if record:
        json.dump({"pid": fake.pid, "dir": old}, open(os.path.join(data, "window.code"), "w"))
    open(os.path.join(data, "window.heartbeat"), "w").write("x")
    payload = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Read", "session_id": "upg", "cwd": "x"})
    env = dict(os.environ, CLAUDE_PROGRESS_DATA=data)
    subprocess.run([sys.executable, os.path.join(REPO, "scripts", "hook.py")], input=payload, text=True, env=env, timeout=90)
    time.sleep(2)
    replaced = fake.poll() is not None
    if not replaced:
        fake.kill()
    log = open(os.path.join(data, "hook_events.log")).read() if os.path.exists(os.path.join(data, "hook_events.log")) else ""
    up = [l.split(" | ", 1)[1] for l in log.splitlines() if "window upgrade" in l]
    print("%-46s replaced: %-5s %s" % (case, replaced, up[0] if up else ""))
    return replaced


def run_same_folder(case, recorded):
    """The window runs from the hook's own folder (local install updated in place) but recorded an older version."""
    shutil.rmtree(T, ignore_errors=True)
    data = os.path.join(T, "data")
    os.makedirs(os.path.join(data, "jobs"))
    here = os.path.join(REPO, "scripts")
    fake = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)", os.path.join(here, NAME)])
    json.dump({"pid": fake.pid, "dir": here, "version": recorded}, open(os.path.join(data, "window.code"), "w"))
    open(os.path.join(data, "window.heartbeat"), "w").write("x")
    payload = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Read", "session_id": "upg", "cwd": "x"})
    subprocess.run([sys.executable, os.path.join(here, "hook.py")], input=payload, text=True,
                   env=dict(os.environ, CLAUDE_PROGRESS_DATA=data), timeout=90)
    time.sleep(2)
    replaced = fake.poll() is not None
    if not replaced:
        fake.kill()
    print("%-46s replaced: %s" % (case, replaced))
    return replaced


results = [
    run_same_folder("same folder, recorded 0.0.1 (updated in place)", "0.0.1") is True,
    run_same_folder("same folder, recorded 99.0.0", "99.0.0") is False,
    run("older 1.0.0, recorded in window.code", "1.0.0", True) is True,
    run("older 1.3.0, not recorded (pre-1.3.1 window)", "1.3.0", False) is True,
    run("newer 9.9.9, recorded: never downgrade", "9.9.9", True) is False,
    run("newer 9.9.9, not recorded: never downgrade", "9.9.9", False) is False,
]
# stop any test window the hook launched for the test data folder
if sys.platform == "win32":
    ps = "Get-CimInstance Win32_Process | ? { $_.CommandLine -like '*cpb_upgrade_test*' } | % { Stop-Process -Id $_.ProcessId -Force }"
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True)
print("RESULT:", "PASS" if all(results) else "FAIL")
sys.exit(0 if all(results) else 1)
