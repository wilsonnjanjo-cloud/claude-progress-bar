"""Claude Progress Bar - platform-neutral logic used by the macOS window (progress_window_mac.py).

The Windows window (progress_window.ps1) implements the same rules in PowerShell. The shared test cases in
tests/test_core.py describe the expected behaviour; keep both implementations passing them.

Nothing here touches the operating system: callers pass in what they measured (file times, CPU seconds, ...).
"""
import json
import os
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


def pattern_regex(pattern):
    """File pattern -> regex (case-insensitive). #### stands for the number and is captured: "f_####.png" matches
    f_0193.png. Without #, * is a wildcard: "*.png" matches any .png (nothing captured)."""
    parts = re.split(r"(#+)", pattern)
    rx = "".join(r"(\d+)" if p.startswith("#") else re.escape(p).replace(r"\*", ".*") for p in parts)
    return re.compile("^" + rx + "$", re.I)


def parse_files(names, pattern, first, last, unit="frames"):
    """Progress from numbered output files: how many of first..last exist among `names` (file names in the folder).
    Returns (pct, text, state)."""
    rx = pattern_regex(pattern)
    first, last = int(first), int(last)
    total = max(1, last - first + 1)
    have = set()
    for n in names:
        m = rx.match(n)
        if m and m.groups():
            k = int(m.group(1))
            if first <= k <= last:
                have.add(k)
    done = len(have)
    if done >= total:
        return 100, "%d/%d %s" % (total, total, unit), "done"
    return int(100 * done / total), "%d/%d %s" % (done, total, unit), "run"


def count_new_files(entries, pattern, since, unit="files", total=None):
    """Files matching `pattern` written since `since` (entries = [(name, mtime)]). With a known total:
    "37/120 images" and a percentage; without: "37 new images" (pct -1). Returns (pct, text, state)."""
    rx = pattern_regex(pattern)
    n = sum(1 for name, mt in entries if mt >= since - 1 and rx.match(name))
    if total:
        return min(100, int(100 * n / total)), "%d/%d %s" % (min(n, total), total, unit), "run"
    return -1, "%d new %s" % (n, unit), "run"


# ---------------------------------------------------------------- recognising countable work from a Claude task

UNITS = r"frames?|images?|photos?|pictures?|files?|pages?|clips?|videos?|items?|tiles?|thumbnails?|slides?|documents?|records?|rows?"


def _unit(word):
    w = word.lower()
    return w if w.endswith("s") else w + "s"


def guess_range(*texts):
    """A numbered range in a task's description or command: "frames 241-264", "pages 1 to 40", "images 1..120".
    Returns (first, last, unit) or None."""
    for t in texts:
        m = re.search(r"\b(%s)\s*(\d+)\s*(?:-|\u2013|to|\.\.)\s*(\d+)" % UNITS, t or "", re.I)
        if m and int(m.group(2)) <= int(m.group(3)) and int(m.group(3)) - int(m.group(2)) < 100000:
            return int(m.group(2)), int(m.group(3)), _unit(m.group(1))
    return None


def guess_count(*texts):
    """A planned count in a task's description: "Convert 120 images", "render 48 frames", "export 12 pages".
    Returns (total, unit) or None."""
    for t in texts:
        m = re.search(r"\b(\d+)\s+(?:[A-Za-z-]+\s+)?(%s)\b" % UNITS, t or "", re.I)
        if m and 0 < int(m.group(1)) < 1000000:
            return int(m.group(1)), _unit(m.group(2))
    return None


def blender_cli(command):
    """Frame range and output from a Blender command line: -s/--frame-start, -e/--frame-end, -f/--render-frame,
    -o/--render-output. Returns dict(first, last, output) with any of them None, or None if not Blender."""
    if not re.search(r"blender(\.exe)?\b", command or "", re.I):
        return None

    def arg(*names):
        for n in names:
            m = re.search(r"(?:^|\s)%s[\s=]+(\"[^\"]*\"|'[^']*'|\S+)" % re.escape(n), command)
            if m:
                return m.group(1).strip("\"'")
        return None
    first, last, frame = arg("-s", "--frame-start"), arg("-e", "--frame-end"), arg("-f", "--render-frame")
    if frame and re.fullmatch(r"\d+", frame):
        first = last = frame
    elif frame and re.fullmatch(r"(\d+)\.\.(\d+)", frame):
        first, last = frame.split("..")
    return {"first": int(first) if first and first.isdigit() else None,
            "last": int(last) if last and last.isdigit() else None,
            "output": arg("-o", "--render-output")}


def blender_output_pattern(output, blend_dir):
    """Blender -o value -> (folder, file pattern). "//frames/f_####" is relative to the .blend; no # means Blender
    appends a 4-digit number. The extension comes from the scene, so any extension matches."""
    if not output:
        return None
    if output.startswith("//"):
        output = os.path.join(blend_dir or "", output[2:])
    folder, base = os.path.split(output.replace("/", os.sep))
    if "#" not in base:
        base += "####"
    return folder, base + ".*"


# working files that appear while any task runs; never counted as output
NOISE = re.compile(r"\.(log|tmp|temp|lock|pyc|jsonl|err|out|heartbeat|job|part|crdownload|swp)$|^~|^\.", re.I)


def numbered_signature(name):
    """f_0193.png -> ("f_####.png", 193); None if the name has no trailing number before its extension."""
    m = re.match(r"^(.*?)(\d+)(\.[A-Za-z0-9]{1,5})$", name)
    if not m:
        return None
    return m.group(1) + "#" * len(m.group(2)) + m.group(3), int(m.group(2))


def pick_output(listing, since, rng=None):
    """Where a task is producing countable output. listing = {folder: [(name, mtime), ...]}.
    Prefers a numbered sequence (f_0193.png ...) with files in the range or written since the task started; otherwise
    a folder where at least 2 files of one type have been written since the start ("*.png").
    Returns (folder, pattern, numbered) or None."""
    best, best_score = None, 0
    for folder, entries in listing.items():
        groups = {}
        for name, mt in entries:
            if NOISE.search(name):
                continue
            sig = numbered_signature(name)
            new = mt >= since - 1
            if sig:
                g = groups.setdefault((sig[0], True), [0, 0, 0])
                g[0] += 1
                g[1] += new
                g[2] += bool(rng) and rng[0] <= sig[1] <= rng[1]
            ext = os.path.splitext(name)[1].lower()
            if ext and new:
                g = groups.setdefault(("*" + ext, False), [0, 0, 0])
                g[0] += 1
                g[1] += 1
        for (pattern, numbered), (total, new, in_range) in groups.items():
            if numbered:
                if total < 2 and not new:
                    continue   # a lone numbered file is not a sequence
                if not (new or in_range):
                    continue
                score = 100000 + new * 1000 + in_range * 10 + total
            else:
                if new < 2:
                    continue   # one new file is not a batch
                score = new
            if score > best_score:
                best, best_score = (folder, pattern, numbered), score
    return best


def parse_output_progress(tail):
    """Progress printed by a background command, from its last few output lines. Returns (pct, text) or None.
    Understands "42%", "12/48", "12 of 48" and Blender's "Fra:193" (no total: frame number only, pct -1)."""
    lines = [l.strip() for l in (tail or "").replace("\r", "\n").splitlines() if l.strip()][-3:]
    for l in reversed(lines):
        m = re.search(r"(\d{1,3}(?:\.\d+)?)\s*%", l)
        if m and float(m.group(1)) <= 100:
            return int(float(m.group(1))), l[:60]
        m = re.search(r"(?<![/\d.])\b(\d+)\s*(?:/|of)\s*(\d+)\b(?![/\d.])", l)   # not dates like 01/07/2026
        if m and 0 < int(m.group(2)) and int(m.group(1)) <= int(m.group(2)):
            return int(100 * int(m.group(1)) / int(m.group(2))), "%s/%s" % (m.group(1), m.group(2))
        m = re.search(r"\bFra:\s*(\d+)", l)
        if m:
            return -1, "frame %s" % m.group(1)
    return None


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
    if kind in ("shell", "fg") and not w.wrote:
        # a command with no output and no files so far (a wait loop, a silent render) is quiet, not stuck
        why = "running, no output"
    if kind == "monitor":
        why = "watching"   # a Monitor waits for events: silence is its normal state; it ends at its own time limit

    if job.get("pid") and pid_alive is False:
        out["state"], out["text"] = "fail", "process ended without finishing"   # caller re-reads the log first
    elif kind == "fg" and elapsed > FG_MAX_S:
        out["state"], out["text"] = "fail", "no result after 11m - session closed or command interrupted"
    elif kind == "agent" and quiet > AGENT_MAX_QUIET:
        out["state"], out["text"] = "fail", "no activity for 1h - session closed or agent stuck"
    elif kind in ("pct", "hf", "ffmpeg", "files") and not job.get("pid") and quiet > MANUAL_MAX_QUIET:
        out["state"], out["text"] = "closed", "closed: no progress for 1h"
    elif quiet > amber and why:
        out["level"], out["text"] = "quiet", "quiet %s" % fmt_age(quiet)   # healthy silence (why): no colour
        w.alerted = False
    elif quiet > red:
        out["level"], out["text"] = "red", "STALLED %s" % fmt_age(quiet)
        if not w.alerted:
            w.alerted = True
            out["alert"] = True
    elif quiet > amber:
        out["level"], out["text"] = "amber", "quiet %s" % fmt_age(quiet)
        w.alerted = False
    else:
        w.alerted = False
    return out


GENERIC = {"running", "working", "starting", "running (foreground)", "watching"}


def status_line(text, pct, state, elapsed_s, note=None):
    """Right-hand status of a row, kept short and always shown whole: count, percentage, time spent, warning.
    Generic words ("running", "working") are left out: the moving bar already says that. Same as Windows."""
    parts = []
    if text and text not in GENERIC:
        parts.append(text if len(text) <= 28 else text[:27] + "\u2026")
    if pct >= 0 and state == "run" and not (text or "").startswith("%d%%" % pct):
        parts.append("%d%%" % pct)
    parts.append(fmt_age(elapsed_s))
    if note:
        parts.append(note)
    return "  \u00b7  ".join(parts)


def details_line(title, key):
    """Text for right-click > Copy task for Claude."""
    return "Please check whether this task is still making progress or has stalled: %s (id %s)" % (title, key)
