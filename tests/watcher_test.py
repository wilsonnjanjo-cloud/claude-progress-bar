"""End-to-end test of automatic counting: the real hook receives a background-task event (as Claude Code sends it),
starts its watcher, and the watcher must find what the task produces, with no registration and no Claude rules.
Run: python tests/watcher_test.py   (Windows or macOS; own data folder, about 30 s)"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = os.path.join(tempfile.gettempdir(), "cpb_watcher_test")
DATA = os.path.join(T, "data")
shutil.rmtree(T, ignore_errors=True)
os.makedirs(os.path.join(DATA, "jobs"))
open(os.path.join(DATA, "window.heartbeat"), "w").write("x")   # pretend a window runs: the test opens none
env = dict(os.environ, CLAUDE_PROGRESS_DATA=DATA)

# project 1: a render that writes frames 241-264 into frames/ (240 older frames already there), plus logs
p1 = os.path.join(T, "demo_scene")
os.makedirs(os.path.join(p1, "frames"))
os.makedirs(os.path.join(p1, "logs"))
for i in range(1, 241):
    open(os.path.join(p1, "frames", "f_%04d.png" % i), "w").close()
    os.utime(os.path.join(p1, "frames", "f_%04d.png" % i), (time.time() - 3600,) * 2)
render = ("import time,os\n"
          "for n in range(241, 265):\n"
          "    open(os.path.join('logs','gpu.log'),'a').write('Fra:%d\\n' % n)\n"
          "    open(os.path.join('frames','f_%04d.png' % n),'w').write('x'); time.sleep(0.5)\n")
open(os.path.join(p1, "fake_render.py"), "w").write(render)

# project 2: a batch that converts 30 images into out/ with plain names (no numbers)
p2 = os.path.join(T, "photos")
os.makedirs(os.path.join(p2, "out"))
batch = ("import time,os\n"
         "for n in 'abcdefghijklmnopqrstuvwxyz0123'[:30]:\n"
         "    open(os.path.join('out','photo_%s.webp' % n),'w').write('x'); time.sleep(0.4)\n")
open(os.path.join(p2, "fake_batch.py"), "w").write(batch)


def start(task_id, folder, script, desc):
    proc = subprocess.Popen([sys.executable, script], cwd=folder)
    cmd = 'cd "%s"; python %s' % (folder, script)
    ev = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": "watchtest-1", "cwd": REPO,
          "tool_use_id": "toolu_" + task_id, "tool_input": {"command": cmd, "description": desc, "run_in_background": True},
          "tool_response": {"backgroundTaskId": task_id}}
    subprocess.run([sys.executable, os.path.join(REPO, "scripts", "hook.py")], input=json.dumps(ev), text=True, env=env, timeout=60)
    return proc


a = start("render01", p1, "fake_render.py", "Render frames 241-264 with GPU+CPU workers")
b = start("batch001", p2, "fake_batch.py", "Convert 30 images to WebP")
deadline = time.time() + 40
found = {}
while time.time() < deadline and len(found) < 2:
    for key in ("watchtes_render01", "watchtes_batch001"):
        try:
            j = json.load(open(os.path.join(DATA, "jobs", key + ".job")))
        except (OSError, ValueError):
            continue
        if j.get("pattern"):
            found[key] = {k: j.get(k) for k in ("out_dir", "pattern", "first", "last", "total", "unit")}
    time.sleep(1)
a.wait(30)
b.wait(30)

sys.path.insert(0, os.path.join(REPO, "scripts"))
import cpb_core as core  # noqa: E402
ok = True
r = found.get("watchtes_render01")
print("render:", r)
if r:
    pct, text, _ = core.parse_files(os.listdir(r["out_dir"]), r["pattern"], r["first"], r["last"], r["unit"])
    print("   bar would read:", text, "%d%%" % pct)
    ok &= os.path.normcase(r["out_dir"]) == os.path.normcase(os.path.join(p1, "frames")) and text == "24/24 frames"
else:
    ok = False
q = found.get("watchtes_batch001")
print("batch: ", q)
if q:
    started = json.load(open(os.path.join(DATA, "jobs", "watchtes_batch001.job")))["started"]
    entries = [(n, os.path.getmtime(os.path.join(q["out_dir"], n))) for n in os.listdir(q["out_dir"])]
    pct, text, _ = core.count_new_files(entries, q["pattern"], started, q["unit"], q["total"])
    print("   bar would read:", text, "%d%%" % pct)
    ok &= os.path.normcase(q["out_dir"]) == os.path.normcase(os.path.join(p2, "out")) and text == "30/30 images"
else:
    ok = False
print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
