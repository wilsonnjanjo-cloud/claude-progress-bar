"""Shared behaviour cases for Claude Progress Bar. Run: python tests/test_core.py
The Windows window (PowerShell) was checked against the same cases with tests/windows_alarm_test.ps1."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import cpb_core as c  # noqa: E402

fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    fails += not ok
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else "   got %r, want %r" % (got, want)))


# --- progress log formats
check("pct: 100% line is done", c.parse_pct("96% (186/192 frames)\n100% (192/192 frames)\n"), (100, "192/192 frames", "done"))
check("pct: NN|text", c.parse_pct("10|loading\n55|encoding\n"), (55, "encoding", "run"))
check("pct: DONE|", c.parse_pct("40|x\nDONE|all good\n"), (100, "all good", "done"))
check("pct: decimal percent, noise after", c.parse_pct("12.5% Rendering\nsome noise line\n"), (12, "Rendering", "run"))
check("pct: FAIL|", c.parse_pct("FAIL|disk full\n"), (100, "disk full", "fail"))
check("pct: nothing yet", c.parse_pct("no progress yet\n"), (0, "starting", "run"))
check("hf: exit 0", c.parse_hf("50%  Rendering frames\nEXIT 0\n")[2], "done")
check("ffmpeg: progress=end", c.parse_ffmpeg("frame=10\nprogress=end\n", 100)[2], "done")

# --- numbered output files
frames = ["f_%04d.png" % i for i in range(1, 233)] + ["notes.txt", "f_0300.jpg"]
check("files: 40 of 193-240 present", c.parse_files(frames, "f_####.png", 193, 240), (83, "40/48 frames", "run"))
check("files: all present is done", c.parse_files(frames + ["f_%04d.png" % i for i in range(233, 241)], "f_####.png", 193, 240), (100, "48/48 frames", "done"))
check("files: none yet", c.parse_files([], "f_####.png", 193, 240), (0, "0/48 frames", "run"))
check("files: case-insensitive, own unit", c.parse_files(["SHOT_007.PNG"], "shot_###.png", 1, 10, "images"), (10, "1/10 images", "run"))

# --- the short right-hand status of a row
check("status: count, percent, time", c.status_line("18/24 frames", 75, "run", 720), "18/24 frames  ·  75%  ·  12m")
check("status: generic word dropped", c.status_line("running", -1, "run", 180, "quiet 2m"), "3m  ·  quiet 2m")
check("status: new files, no total", c.status_line("7 new clips", -1, "run", 95), "7 new clips  ·  1m")
check("status: long text shortened", len(c.status_line("x" * 60, -1, "run", 5).split("  ")[0]), 28)

# --- recognising countable work from a task (no registration, no Claude rules)
check("range from description", c.guess_range("Render frames 241-264 with GPU+CPU workers"), (241, 264, "frames"))
check("range: pages 1 to 40", c.guess_range("Export pages 1 to 40 as PNG"), (1, 40, "pages"))
check("range: none", c.guess_range("Write frame progress percentage log"), None)
check("count from description", c.guess_count("Convert 120 images to WebP"), (120, "images"))
check("count with an adjective", c.guess_count("Download 12 product photos"), (12, "photos"))
check("count: none", c.guess_count("Run the tests"), None)
check("blender -s/-e/-o", c.blender_cli('blender -b "a b/scene.blend" -o //out/f_#### -s 10 -e 20 -a'),
      {"first": 10, "last": 20, "output": "//out/f_####"})
check("blender -f single frame", c.blender_cli("blender.exe -b x.blend -f 7")["first"], 7)
check("not blender", c.blender_cli("python render.py"), None)
check("blender output pattern", c.blender_output_pattern("//out/f_", "/p")[1], "f_####.*")
t0 = 1000.0
listing = {"/p/frames": [("f_%04d.png" % i, 900.0) for i in range(1, 241)] + [("f_%04d.png" % i, 1010.0) for i in range(241, 259)],
           "/p/logs": [("gpu.log", 1012.0), ("cpu1.log", 1012.0)],
           "/p": [("demo_scene.blend", 500.0)]}
check("finds the frames folder", c.pick_output(listing, t0, (241, 264)), ("/p/frames", "f_####.png", True))
check("count frames found", c.parse_files([n for n, _ in listing["/p/frames"]], "f_####.png", 241, 264), (75, "18/24 frames", "run"))
batch = {"/w/out": [("photo_a.jpg", 1005.0), ("photo_b.jpg", 1006.0), ("photo_c.jpg", 1007.0)], "/w": [("run.log", 1008.0)]}
check("finds a batch of new files", c.pick_output(batch, t0), ("/w/out", "*.jpg", False))
check("count new files vs planned total", c.count_new_files(batch["/w/out"], "*.jpg", t0, "images", 120), (2, "3/120 images", "run"))
check("count new files, no total", c.count_new_files(batch["/w/out"], "*.jpg", t0, "images"), (-1, "3 new images", "run"))
check("logs alone are not output", c.pick_output({"/w": [("a.log", 1005.0), ("b.log", 1006.0)]}, t0), None)
check("one new file is not a batch", c.pick_output({"/w": [("a.png", 1005.0)]}, t0), None)
check("old files only: nothing yet", c.pick_output({"/w": [("a.png", 10.0), ("b.png", 11.0)]}, t0), None)

# --- progress printed by background commands
check("output: percent", c.parse_output_progress("loading\nRendering 42%\n"), (42, "Rendering 42%"))
check("output: N/M", c.parse_output_progress("frame 12/48 done\n"), (25, "12/48"))
check("output: N of M", c.parse_output_progress("item 3 of 4\n"), (75, "3/4"))
check("output: Blender Fra:", c.parse_output_progress("Fra:193 Mem:512M | Rendering 3/40 samples\nFra:195 Mem:512M\n"), (-1, "frame 195"))
check("output: a date is not progress", c.parse_output_progress("saved 11/07/2026\n"), None)
check("output: a 01/07 date is not progress", c.parse_output_progress("run on 01/07/2026\n"), None)
check("output: nothing", c.parse_output_progress("hello\n"), None)

# --- transcripts
wait = '{"type":"user","message":{"content":[{"type":"text","text":"go"}]}}\n' \
       '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"t1"}]}}\n'
stuck = '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"t1"}]}}\n' \
        '{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"t1"}]}}\n'
check("agent ending on tool_use is waiting", c.agent_waiting(wait), True)
check("agent after tool_result is not waiting", c.agent_waiting(stuck), False)
check("agent: cut-off first line ignored", c.agent_waiting('xt":"go"}]}}\n' + wait.splitlines()[1]), True)
note = r'<task-notification>\n<task-id>bh24k9pep</task-id>\n<tool-use-id>toolu_1</tool-use-id>\n<status>completed</status>'
check("shell status from transcript", c.shell_status(note, "bh24k9pep"), "completed")
check("shell status: other task", c.shell_status(note, "zzz"), None)
check("fg finished from transcript", c.fg_finished('{"tool_use_id":"toolu_9"}', "toolu_9"), True)


# --- alarm rule: run each case for 50 simulated seconds of 2-second ticks, like the window does
def simulate(job, state="run", log_age=360, cpu=None, alive=None, waiting=False, wrote_earlier=True):
    w = c.Watch()
    now0 = 1_000_000.0
    started = now0 - 480
    log_time = now0 - log_age if wrote_earlier else started
    alerts, last = 0, None
    for i in range(26):
        now = now0 + 2 * i
        cpu_s = None if cpu is None else (cpu(i))
        last = c.judge(w, job, state, now, started, log_time, cpu_seconds=cpu_s, pid_alive=alive, waiting_on_tool=waiting)
        alerts += last["alert"]
    return alerts, last


busy_cpu = lambda i: 10.0 + i * 1.5     # noqa: E731  CPU time keeps rising
idle_cpu = lambda i: 10.0               # noqa: E731  alive but flat

a, r = simulate({"kind": "agent", "stall": 60}, waiting=True)
check("agent waiting on a tool: no alarm", (a, r["level"]), (0, "quiet"))
a, r = simulate({"kind": "agent", "stall": 60})
check("agent quiet after a result: alarm", (a, r["level"]), (1, "red"))
a, r = simulate({"kind": "pct", "pid": 1, "stall": 30}, cpu=busy_cpu, alive=True)
check("render, busy process: no alarm", (a, r["level"]), (0, "quiet"))
a, r = simulate({"kind": "pct", "pid": 1, "stall": 30}, cpu=idle_cpu, alive=True)
check("render, alive but idle (hung): alarm", (a, r["level"]), (1, "red"))
a, r = simulate({"kind": "pct", "stall": 30}, state="done")
check("render finished at 100%: no alarm", (a, r["state"]), (0, "done"))
a, r = simulate({"kind": "pct", "stall": 30})
check("render, no pid, quiet: alarm", (a, r["level"]), (1, "red"))
a, r = simulate({"kind": "shell", "stall": 30}, wrote_earlier=False)
check("silent background command: no alarm", (a, r["level"]), (0, "quiet"))
a, r = simulate({"kind": "fg", "stall": 30}, wrote_earlier=False)
check("silent foreground wait loop: no alarm", (a, r["level"]), (0, "quiet"))
a, r = simulate({"kind": "monitor", "stall": 30}, wrote_earlier=False)
check("silent Monitor watch: no alarm", (a, r["level"]), (0, "quiet"))
a, r = simulate({"kind": "monitor", "stall": 30})
check("Monitor quiet after an event: no alarm", (a, r["level"]), (0, "quiet"))
a, r = simulate({"kind": "fg", "stall": 30})
check("foreground that wrote, then stopped: alarm", (a, r["level"]), (1, "red"))
a, r = simulate({"kind": "pct"}, log_age=4000)
check("no pid, silent 1h+: closed quietly", (a, r["state"]), (0, "closed"))
a, r = simulate({"kind": "pct", "pid": 1}, alive=False)
check("process ended mid-run: failed", r["state"], "fail")

# learned rhythm: a job that normally pauses 3 min should not alarm at 6 min
w = c.Watch()
t0 = 1_000_000.0
for k in range(4):  # log updates every 180 s
    c.judge(w, {"kind": "pct"}, "run", t0 + 180 * k, t0 - 10, t0 + 180 * k)
r = c.judge(w, {"kind": "pct"}, "run", t0 + 540 + 360, t0 - 10, t0 + 540)
check("learned 3-min rhythm: 6 min quiet is not red", r["level"], "amber")

print("\n%d failed" % fails if fails else "\nall passed")
sys.exit(1 if fails else 0)
