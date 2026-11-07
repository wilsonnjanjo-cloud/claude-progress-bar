"""Smoke test for the Python window (progress_window_mac.py). Mac testers: please run this and paste the output
into the "macOS testers" issue.

  python3 tests/window_smoke_test.py

It opens a test copy of the window (own data folder, alarm sound muted) for about 60 seconds with six fake tasks,
then reports which ones raised a stall alarm. Expected: exactly "agent stuck", "render hung process",
"render no pid quiet". It does not touch your real Claude Progress Bar data.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WINDOW = os.path.join(ROOT, "scripts", "progress_window_mac.py")
T = os.path.join(tempfile.gettempdir(), "cpb_window_smoke")
D = os.path.join(T, "data")

shutil.rmtree(T, ignore_errors=True)
os.makedirs(os.path.join(D, "jobs"))
open(os.path.join(D, "alarm.muted"), "w").write("muted")
old = time.time() - 360
start = time.time() - 480


def write(name, text):
    p = os.path.join(T, name)
    with open(p, "w") as f:
        f.write(text)
    os.utime(p, (old, old))
    return p


def job(key, **kw):
    kw["started"] = start
    json.dump(kw, open(os.path.join(D, "jobs", key + ".job"), "w"))


tool_use = '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"t1","name":"Bash"}]}}\n'
tool_result = '{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"t1"}]}}\n'
job("A1", kind="agent", name="agent waiting on tool", state="run", stall=60,
    log=write("agent_wait.jsonl", '{"type":"user","message":{"content":[{"type":"text","text":"go"}]}}\n' + tool_use))
job("A2", kind="agent", name="agent stuck", state="run", stall=60, log=write("agent_stuck.jsonl", tool_use + tool_result))
busy = subprocess.Popen([sys.executable, "-c", "while True: pass"])
idle = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
job("M3", kind="pct", name="render busy CPU", pid=busy.pid, stall=30, log=write("busy.txt", "40% (77/192 frames)\n"))
job("M4", kind="pct", name="render hung process", pid=idle.pid, stall=30, log=write("idle.txt", "40% (77/192 frames)\n"))
job("M5", kind="pct", name="render finished", stall=30, log=write("done.txt", "100% (192/192 frames)\n"))
job("M6", kind="pct", name="render no pid quiet", stall=30, log=write("nopid.txt", "40% (77/192 frames)\n"))

win = subprocess.Popen([sys.executable, WINDOW, "--data", D], stderr=subprocess.PIPE, text=True)
try:
    time.sleep(60)
finally:
    alive = win.poll() is None
    for p in (win, busy, idle):
        p.kill()
err = win.stderr.read().strip()
log = open(os.path.join(D, "hook_events.log")).read() if os.path.exists(os.path.join(D, "hook_events.log")) else ""
alarms = sorted(l.split("stall alarm | ", 1)[1] for l in log.splitlines() if "stall alarm | " in l)
errors = open(os.path.join(D, "hook_errors.log")).read().strip() if os.path.exists(os.path.join(D, "hook_errors.log")) else ""
expected = ["agent stuck", "render hung process", "render no pid quiet"]
print("platform:", sys.platform, "| python", sys.version.split()[0])
print("window ran the whole time:", alive)
print("alarms:", alarms)
print("window errors:", errors or "none")
print("stderr:", err or "none")
ok = alive and alarms == expected and not errors and not err
print("RESULT:", "PASS" if ok else "FAIL (expected alarms %s)" % expected)
sys.exit(0 if ok else 1)
