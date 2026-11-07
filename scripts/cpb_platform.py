"""Claude Progress Bar - every operating-system call the macOS window makes, in one place.

macOS is the target. The Windows branches exist only so the window can be smoke-tested on Windows; the real Windows
window is progress_window.ps1. If something misbehaves on a Mac, the culprit is almost certainly in this file.
"""
import os
import subprocess
import sys

MAC = sys.platform == "darwin"
WIN = sys.platform == "win32"


def _run(args, timeout=3):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None


def pid_alive(pid):
    """True if the process exists."""
    pid = int(pid)
    if WIN:
        # never os.kill(pid, 0) on Windows: it terminates the process
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    except OSError:
        return False


def cpu_seconds(pid):
    """Total CPU time (user + system) used so far by the process, or None if unknown."""
    pid = int(pid)
    if WIN:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not h:
            return None
        t = [ctypes.c_ulonglong() for _ in range(4)]
        ok = ctypes.windll.kernel32.GetProcessTimes(h, *[ctypes.byref(x) for x in t])
        ctypes.windll.kernel32.CloseHandle(h)
        return (t[2].value + t[3].value) / 1e7 if ok else None
    r = _run(["ps", "-o", "time=", "-p", str(pid)])
    if not r or r.returncode != 0 or not r.stdout.strip():
        return None
    return parse_ps_time(r.stdout.strip())


def parse_ps_time(s):
    """ps TIME column: [[dd-]hh:]mm:ss[.cc] -> seconds."""
    days = 0
    if "-" in s:
        d, s = s.split("-", 1)
        days = int(d)
    parts = [float(p) for p in s.split(":")]
    sec = 0.0
    for p in parts:
        sec = sec * 60 + p
    return days * 86400 + sec


def file_in_use(path):
    """True if some process has the file open (a background command still writing its output), False if not,
    None if it cannot be told."""
    if WIN:
        return None  # not needed: the Windows window uses its own file-sharing check
    r = _run(["lsof", "-t", "--", path], timeout=4)
    if r is None:
        return None
    return bool(r.stdout.strip())


def notify(title, message):
    """A macOS notification. Text is passed as arguments, never pasted into the script (task names can have quotes)."""
    if not MAC:
        return
    script = 'on run argv\ndisplay notification (item 2 of argv) with title (item 1 of argv)\nend run'
    try:
        subprocess.Popen(["osascript", "-e", script, str(title)[:120], str(message)[:200]],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def play_alert():
    if MAC:
        for snd in ("/System/Library/Sounds/Sosumi.aiff", "/System/Library/Sounds/Funk.aiff"):
            if os.path.exists(snd):
                try:
                    subprocess.Popen(["afplay", snd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception:
                    pass
                return
    elif WIN:
        try:
            import winsound
            winsound.MessageBeep(0x30)
        except Exception:
            pass


def copy_text(text):
    """Copy to the clipboard. Returns True on success (the window falls back to Tk's clipboard otherwise)."""
    if MAC:
        try:
            subprocess.run(["pbcopy"], input=text, text=True, timeout=3, check=True)
            return True
        except Exception:
            return False
    return False


class SingleInstance:
    """Holds an exclusive lock on a file for the life of the process; a second window exits at once."""

    def __init__(self, path):
        self.path, self.fh, self.ok = path, None, False
        try:
            self.fh = open(path, "a+")
            if WIN:
                import msvcrt
                self.fh.seek(0)
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.ok = True
        except OSError:
            self.ok = False
