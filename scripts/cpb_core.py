"""Claude Progress Bar - platform-neutral logic used by the macOS window (progress_window_mac.py).

The Windows window (progress_window.ps1) implements the same rules in PowerShell. The shared test cases in
tests/test_core.py describe the expected behaviour; keep both implementations passing them.

Nothing here touches the operating system: callers pass in what they measured (file times, CPU seconds, ...).
"""
import json
import re

WARN_AFTER = 120   # amber: quiet this long (no sound)
STALL_AFTER = 300  # red + sound: quiet this long AND evidence of a stall
FG_MIN_S = 10      # foreground commands get a bar only after running this long
FG_MAX_S = 660     # a foreground command with no result after this long is closed
AGENT_MAX_QUIET = 3600
MANUAL_MAX_QUIET = 3600


def fmt_age(sec):
    if sec < 90:
        return "%ds" % sec
    if sec < 5400:
        return "%dm" % (sec / 60)
    return "%.1fh" % (sec / 3600)


# ---------------------------------------------------------------- progress logs (hand-registered jobs)

def parse_pct(tail):
    """pct logs: last line that carries progress. Returns (pct, text, state); state run | done | fail.
    Accepts "NN|text", "NN% text", "NN.N% (12/192 frames)", "DONE|text", "FAIL|text". 100% counts as done."""
    lines = [l.strip() for l in (tail or "").splitlines() if l.strip()]
    for l in reversed(lines):
        m = re.match(r"^(DONE|FAIL)\s*\|(.*)$", l)
        if m:
            return 100, m.group(2).strip(), "done" if m.group(1) == "DONE" else "fail"
        m = re.match(r"^\s*(\d{1,3}(?:\.\d+)?)\s*(?:\||%)\s*(.*)$", l)
        if m:
            p = int(float(m.group(1)))
            txt = m.group(2).strip(" |-()") or "running"
            if p >= 100:
                return 100, ("finished" if txt == "running" else txt), "done"
            return max(0, p), txt, "run"
    return 0, "starting", "run"


def parse_hf(tail):
    """HyperFrames render log: "NN%  Phase" lines, ends with "EXIT rc"."""
    t = tail or ""
    m = re.search(r"EXIT (-?\d+)", t)
    if m:
        return (100, "done", "done") if m.group(1) == "0" else (100, "failed (exit %s)" % m.group(1), "fail")
    allm = re.findall(r"(\d{1,3})%\s+([A-Za-z][A-Za-z ]{2,40})", t)
    if allm:
        p, txt = allm[-1]
        return int(p), txt.strip().lower(), "run"
    return 0, "starting", "run"


def parse_ffmpeg(tail, total):
    """ffmpeg -progress file: frame=N lines, progress=end when finished."""
    t = tail or ""
    if "progress=end" in t:
        return 100, "done", "done"
    frames = re.findall(r"frame=(\d+)", t)
    if frames:
        f = int(frames[-1])
        return min(99, int(100 * f / max(1, int(total or 0)))), "frame %d / %s" % (f, total), "run"
    return 0, "starting", "run"


# ---------------------------------------------------------------- transcripts

def agent_waiting(tail):
    """True when an agent transcript ends on a tool call with no result yet (waiting on that tool, not stuck)."""
    for l in reversed((tail or "").splitlines()):
        if not l.strip():
            continue
        try:
            o = json.loads(l)
        except ValueError:
            continue  # the first line of a tail can be cut off
        if o.get("type") == "assistant":
            content = (o.get("message") or {}).get("content") or []
            return any(isinstance(c, dict) and c.get("type") == "tool_use" for c in content)
        if o.get("type") == "user":
            return False
    return False


def fg_finished(transcript_tail, tuid):
    """A foreground command is over once its result is in the session transcript (covers denied/interrupted calls)."""
    return bool(tuid) and ('"tool_use_id":"%s"' % tuid) in (transcript_tail or "")


def shell_status(transcript_tail, tid):
    """Background command status from Claude Code's task notification in the session transcript:
    "completed" / "failed" / "killed" ..., or None if it has not finished (or the notice is not there)."""
    if not tid:
        return None
    m = re.search(r"<task-id>%s</task-id>.{0,1500}?<status>(\w+)</status>" % re.escape(tid), transcript_tail or "", re.S)
    return m.group(1) if m else None


# ---------------------------------------------------------------- stall judgement

class Watch:
    """Per-row memory used by judge(): the job's own rhythm and its process's CPU use."""

    def __init__(self):
        self.last_log_time = None
        self.max_gap = 0.0
        self.wrote = False
        self.cpu_prev = None
        self.cpu_busy_at = None
        self.alerted = False


def judge(w, job, state, now, started, log_time, cpu_seconds=None, pid_alive=None, waiting_on_tool=False):
    """Decide how a running job looks. Silence alone is never an alarm.

    w            Watch for this row (updated in place)
    job          the job dict (kind, pid, stall, ...)
    state        run | done | fail from the job's own source
    now/started/log_time  seconds (epoch); log_time = last time its log/output/transcript changed
    cpu_seconds  total CPU seconds of job["pid"] (None if unknown)
    pid_alive    True/False if job has a pid, else None
    waiting_on_tool  agent transcript ends on a pending tool call

    Returns dict(state, text, level, quiet, alert): level ok | quiet | amber | red; alert True once when it turns red.
    text is a note to show (or None)."""
    kind = job.get("kind")
    quiet = max(0.0, now - log_time)
    elapsed = now - started
    # learn the job's rhythm: longest normal gap between log updates
    if w.last_log_time is not None and log_time > w.last_log_time:
        w.max_gap = max(w.max_gap, log_time - w.last_log_time)
        w.wrote = True
    if log_time > started + 1:
        w.wrote = True
    w.last_log_time = log_time

    red = min(float(job.get("stall") or STALL_AFTER), STALL_AFTER)
    red = max(red, 3 * w.max_gap)
    amber = min(WARN_AFTER, red / 2)
    out = {"state": state, "text": None, "level": "ok", "quiet": quiet, "alert": False}
    if state != "run":
        return out

    why = None
    if job.get("pid") and pid_alive:
        if cpu_seconds is not None:
            if w.cpu_prev is None or cpu_seconds > w.cpu_prev + 0.05:
                w.cpu_busy_at = now
            w.cpu_prev = cpu_seconds
            if w.cpu_busy_at is not None and now - w.cpu_busy_at < red:
                why = "process busy"
    if kind == "agent" and quiet > amber and waiting_on_tool:
        why = "waiting on a tool"
    if kind == "shell" and not w.wrote:
        why = "running, no output"

    if job.get("pid") and pid_alive is False:
        out["state"], out["text"] = "fail", "process ended without finishing"   # caller re-reads the log first
    elif kind == "fg" and elapsed > FG_MAX_S:
        out["state"], out["text"] = "fail", "no result after 11m - session closed or command interrupted"
    elif kind == "agent" and quiet > AGENT_MAX_QUIET:
        out["state"], out["text"] = "fail", "no activity for 1h - session closed or agent stuck"
    elif kind in ("pct", "hf", "ffmpeg") and not job.get("pid") and quiet > MANUAL_MAX_QUIET:
        out["state"], out["text"] = "closed", "closed: no progress for 1h"
    elif quiet > amber and why:
        out["level"], out["text"] = "quiet", "%s, quiet %s" % (why, fmt_age(quiet))
        w.alerted = False
    elif quiet > red:
        out["level"], out["text"] = "red", "STALLED [no activity for %s]" % fmt_age(quiet)
        if not w.alerted:
            w.alerted = True
            out["alert"] = True
    elif quiet > amber:
        out["level"], out["text"] = "amber", "[no activity for %s]" % fmt_age(quiet)
        w.alerted = False
    else:
        w.alerted = False
    return out


def details_line(title, key):
    """Text for right-click > Copy task for Claude."""
    return "Please check whether this task is still making progress or has stalled: %s (id %s)" % (title, key)
