"""Register a long job by hand with Claude Progress Bar, for a real % bar (macOS; also works on Windows).

  python3 progress_job.py --name "Render" --kind pct --log ~/work/progress.txt --pid 1234
  python3 progress_job.py --name "Master" --kind ffmpeg --log ~/work/prog.txt --total 1051 --pid 5678

pct logs: write "NN|text" or "NN% text" lines and end with "DONE|text" (or "FAIL|text"); a 100% line also counts as done.
Always pass --pid when you can: the bar then tells a busy job from a hung one, and knows the moment it ends.
"""
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hook  # noqa: E402  (same data folder and window launcher as the Claude hooks)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True)
    ap.add_argument("--kind", choices=["pct", "hf", "ffmpeg"], default="pct")
    ap.add_argument("--log", required=True, help="progress file the job writes")
    ap.add_argument("--pid", type=int, default=0, help="the working process (render/encode), not a wrapper shell")
    ap.add_argument("--total", type=int, default=0, help="ffmpeg: total frames")
    ap.add_argument("--stall", type=int, default=300, help="seconds of silence before a stall can be reported")
    ap.add_argument("--project", default="")
    a = ap.parse_args()

    log = os.path.abspath(os.path.expanduser(a.log))
    os.makedirs(hook.JOBS, exist_ok=True)
    # a re-run of the same job (same log file) replaces its earlier registration
    for name in os.listdir(hook.JOBS):
        if name.startswith("manual_") and name.endswith(".job"):
            p = os.path.join(hook.JOBS, name)
            try:
                if json.load(open(p, encoding="utf-8-sig")).get("log") == log:
                    os.remove(p)
            except (OSError, ValueError):
                pass
    job = {"kind": a.kind, "name": a.name, "log": log, "total": a.total, "stall": a.stall, "started": time.time()}
    if a.pid:
        job["pid"] = a.pid
    if a.project:
        job["project"] = a.project
    key = re.sub(r"[^A-Za-z0-9._-]+", "_", a.name) + "_" + time.strftime("%H%M%S")
    with open(os.path.join(hook.JOBS, "manual_%s.job" % key), "w", encoding="utf-8") as f:
        json.dump(job, f)
    hook.ensure_window()
    print("registered: manual_%s" % key)


if __name__ == "__main__":
    main()
