"""Claude Progress Bar - macOS window (beta). Started by hook.py; one copy runs at a time.

Shows one bar per Claude Code background command, subagent and long foreground command (task files written by
hook.py into the data folder), plus jobs registered with progress_job.py. Checks every 2 s.
Silence alone never alarms: see cpb_core.judge(). Uses tkinter (part of the python.org Python; Homebrew needs
`brew install python-tk`). OS calls live in cpb_platform.py.
"""
import argparse
import glob
import json
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cpb_core as core  # noqa: E402
import cpb_platform as plat  # noqa: E402

PROJECTS = os.path.join(os.path.expanduser("~"), ".claude", "projects")


def default_data_dir():
    if os.environ.get("CLAUDE_PROGRESS_DATA"):
        return os.environ["CLAUDE_PROGRESS_DATA"]
    if plat.WIN:
        return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "ClaudeProgressBar")
    return os.path.join(os.path.expanduser("~"), "Library", "Application Support", "ClaudeProgressBar")


def read_tail(path, n=8192):
    if not path or not os.path.exists(path):
        return ""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - n))
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def mtime(path):
    try:
        return os.path.getmtime(path)
    except (OSError, TypeError):
        return None


def folder_entries(folder):
    """[(name, mtime, size)] of the files in a folder, or None if it cannot be read."""
    try:
        out = []
        for e in os.scandir(folder):
            if e.is_file():
                st = e.stat()
                out.append((e.name, st.st_mtime, st.st_size))
        return out
    except OSError:
        return None


class Row:
    def __init__(self, key):
        self.key = key
        self.watch = core.Watch()
        self.ended = None
        self.title = key
        self.desc = None
        self.job = {}
        self.frame = self.label = self.bar = None
        self.mode = None
        self.level = "ok"


class App:
    COLORS = {"ok": "black", "quiet": "black", "amber": "#c2410c", "red": "#dc2626",
              "done": "#15803d", "fail": "#991b1b", "closed": "#6b7280"}

    def __init__(self, tk, ttk, data):
        self.tk, self.ttk, self.data = tk, ttk, data
        self.jobs = os.path.join(data, "jobs")
        self.heartbeat = os.path.join(data, "window.heartbeat")
        self.event_log = os.path.join(data, "hook_events.log")
        self.mute_flag = os.path.join(data, "alarm.muted")
        self.rows = {}
        self.last_active = time.time()
        self.transcripts = {}
        self.agent_logs, self.agent_look = {}, {}
        self.menu_key = None
        self.title_reset = None
        self.last_error = None

        self.root = root = tk.Tk()
        root.title("Claude Progress Bar")
        root.resizable(False, False)
        root.attributes("-topmost", True)
        sw = root.winfo_screenwidth()
        self.pos = (max(0, sw - 600), 60)
        root.geometry("560x80+%d+%d" % self.pos)
        self.box = tk.Frame(root)
        self.box.pack(fill="both", expand=True, padx=6, pady=4)

        # app menu (macOS shows it in the menu bar at the top of the screen)
        self.muted = tk.BooleanVar(value=os.path.exists(self.mute_flag))
        bar = tk.Menu(root)
        m = tk.Menu(bar, tearoff=0)
        m.add_checkbutton(label="Mute alarm sound", variable=self.muted, command=self.toggle_mute)
        m.add_command(label="Hide window", command=self.hide)
        m.add_separator()
        m.add_command(label="Quit Claude Progress Bar (restarts with the next Claude task)", command=self.quit)
        bar.add_cascade(label="Progress Bar", menu=m)
        root.config(menu=bar)
        root.protocol("WM_DELETE_WINDOW", self.hide)   # the close button only hides it; it keeps listening
        try:
            root.createcommand("::tk::mac::ReopenApplication", self.show)  # clicking the Dock icon shows it again
        except Exception:
            pass

        # right-click menu for one row
        self.menu = tk.Menu(root, tearoff=0)
        self.menu.add_command(label="Copy task for Claude", command=self.copy_details)
        self.menu.add_command(label="Copy log path", command=self.copy_path)
        self.menu.add_separator()
        self.menu.add_command(label="Dismiss this bar", command=self.dismiss)

        self.log("listener started", "pid %d" % os.getpid(), "macos" if plat.MAC else sys.platform)
        try:  # which code this window runs, so a newer installed version can replace it (hook.py upgrade_window)
            with open(os.path.join(data, "window.code"), "w", encoding="utf-8") as f:
                try:
                    ver = json.load(open(os.path.join(HERE, "..", ".claude-plugin", "plugin.json"), encoding="utf-8"))["version"]
                except Exception:
                    ver = ""
                json.dump({"pid": os.getpid(), "dir": HERE, "version": ver}, f)
        except OSError:
            pass
        root.after(200, self.tick)

    # ------------------------------------------------------------ helpers
    def log(self, *parts):
        try:
            with open(self.event_log, "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + " | ".join(str(p) for p in parts) + "\n")
        except OSError:
            pass

    def log_error(self, text):
        if text == self.last_error:
            return
        self.last_error = text
        try:
            with open(os.path.join(self.data, "hook_errors.log"), "a", encoding="utf-8") as f:
                f.write("%s window: %s\n" % (time.ctime(), text))
        except OSError:
            pass

    def show(self, *_):
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)

    def hide(self):
        self.root.withdraw()

    def quit(self):
        self.log("listener exited", "quit from menu")
        for p in (self.heartbeat, self.heartbeat + ".launch"):
            try:
                os.remove(p)
            except OSError:
                pass
        self.root.destroy()

    def toggle_mute(self):
        if self.muted.get():
            open(self.mute_flag, "w").write("muted")
        else:
            try:
                os.remove(self.mute_flag)
            except OSError:
                pass

    def set_title_note(self, note):
        self.root.title("Claude Progress Bar  -  " + note)
        self.title_reset = time.time() + 4

    def clip(self, text, what):
        if not plat.copy_text(text):
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
        self.set_title_note("copied: " + what)

    def copy_details(self):
        r = self.rows.get(self.menu_key)
        if r:
            self.clip(core.details_line(r.title, r.key), r.title)

    def copy_path(self):
        r = self.rows.get(self.menu_key)
        if r and r.job.get("log"):
            self.clip(r.job["log"], "log path")

    def dismiss(self):
        k = self.menu_key
        if k in self.rows:
            try:
                os.remove(os.path.join(self.jobs, k + ".job"))
            except OSError:
                pass
            self.rows[k].frame.destroy()
            del self.rows[k]
            self.log("bar dismissed by user", k)

    def popup(self, event, key):
        self.menu_key = key
        r = self.rows.get(key)
        if not r:
            return
        name = r.title if len(r.title) <= 50 else r.title[:50] + "..."
        self.menu.entryconfigure(0, label="Copy task for Claude:  " + name)
        self.menu.entryconfigure(1, state="normal" if r.job.get("log") else "disabled")
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def alert(self, title):
        if not self.muted.get():
            plat.play_alert()
        plat.notify("Claude task stalled", title)
        self.log("stall alarm", title)
        self.show()

    # ------------------------------------------------------------ rows
    def new_row(self, key):
        tk, ttk = self.tk, self.ttk
        r = Row(key)
        r.frame = tk.Frame(self.box)
        # task name on the left (shortened to fit), status on the right (always whole)
        top = tk.Frame(r.frame)
        top.pack(fill="x")
        r.status = tk.Label(top, text="", anchor="e")
        r.status.pack(side="right")
        r.label = tk.Label(top, text=key, anchor="w")
        r.label.pack(side="left", fill="x", expand=True)
        r.top = top
        r.bar = ttk.Progressbar(r.frame, length=530, maximum=100, mode="determinate")
        r.bar.pack(fill="x", pady=(1, 4))
        r.frame.pack(fill="x")
        for w in (r.frame, r.top, r.label, r.status, r.bar):
            for seq in ("<Button-2>", "<Button-3>", "<Control-Button-1>"):
                w.bind(seq, lambda e, k=key: self.popup(e, k))
        self.rows[key] = r
        return r

    def set_row_text(self, r, title, status, color):
        """Status gets the room it needs at the right; the name is cut with an ellipsis to fit the rest."""
        import tkinter.font as tkfont
        font = tkfont.nametofont(r.label.cget("font")) if not isinstance(r.label.cget("font"), tuple) else tkfont.Font(font=r.label.cget("font"))
        room = 540 - font.measure(status) - 24
        name = title
        if font.measure(name) > room:
            while name and font.measure(name + "\u2026") > room:
                name = name[:-1]
            name = name.rstrip() + "\u2026"
        r.status.configure(text=status, fg=color)
        r.label.configure(text=name, fg=color)

    def set_mode(self, r, mode):
        if r.mode == mode:
            return
        r.mode = mode
        if mode == "indeterminate":
            r.bar.configure(mode="indeterminate")
            r.bar.start(15)
        else:
            r.bar.stop()
            r.bar.configure(mode="determinate")

    # ------------------------------------------------------------ task state
    def transcript_for(self, job):
        tp = job.get("transcript")
        if tp and os.path.exists(tp):
            return tp
        sess = job.get("session")
        if sess and sess not in self.transcripts:
            hits = glob.glob(os.path.join(PROJECTS, "*", sess + ".jsonl"))
            self.transcripts[sess] = hits[0] if hits else None
        return self.transcripts.get(sess)

    def agent_log(self, key, job):
        # a session can move to another project folder; find the agent transcript by session + agent id (cached)
        if job.get("log") and os.path.exists(job["log"]):
            return job["log"]
        if self.agent_logs.get(key):
            return self.agent_logs[key]
        if time.time() - self.agent_look.get(key, 0) < 30:
            return job.get("log")
        self.agent_look[key] = time.time()
        sess, _, aid = key.partition("_")
        hits = glob.glob(os.path.join(PROJECTS, "*", sess + "*", "subagents", "agent-%s.jsonl" % aid))
        if hits:
            self.agent_logs[key] = hits[0]
            return hits[0]
        return job.get("log")

    @staticmethod
    def counted_output(job, started):
        """(pct, text) from the output the hook's watcher found for a Claude task (out_dir + pattern), or None."""
        folder, pattern = job.get("out_dir"), job.get("pattern")
        if not folder or not pattern:
            return None
        entries = folder_entries(folder)
        if entries is None:
            return None
        if job.get("first") is not None and job.get("last") is not None:
            # finished frames only (non-empty), and only new ones after a re-render over existing frames
            return core.count_range(entries, pattern, job["first"], job["last"], job.get("unit") or "frames",
                                    since=job.get("count_since"))
        pct, text, _ = core.count_new_files(entries, pattern, started, job.get("unit") or "files", job.get("total"))
        return pct, text

    def get_state(self, job, now, started):
        kind = job.get("kind")
        if kind == "monitor":
            # a Monitor watch: ends when its command ends (task notice / output released) or at its time limit
            limit = float(job.get("timeout") or 0)
            if limit and now - started > limit + 5:
                return 100, "stopped (time limit)", "done"
            st = core.shell_status(read_tail(self.transcript_for(job), 400000), job.get("tid"))
            if st:
                return 100, "finished", "done"
            log = job.get("log")
            if log and os.path.exists(log) and plat.file_in_use(log) is False and now - (mtime(log) or now) > 10:
                return 100, "finished", "done"
            return (-1, "stops in %s" % core.fmt_age(limit - (now - started)), "run") if limit else (-1, "watching", "run")
        if kind == "shell":
            st = core.shell_status(read_tail(self.transcript_for(job), 400000), job.get("tid"))
            if st:
                return (100, "finished", "done") if st == "completed" else (100, st, "fail")
            log = job.get("log")
            if not log or not os.path.exists(log):
                return (-1, "running", "run") if now - started < 60 else (100, "finished", "done")
            if plat.file_in_use(log) is False and now - (mtime(log) or now) > 10:
                return 100, "finished", "done"
            counted = self.counted_output(job, started)   # output files the hook's watcher found for this task
            if counted:
                return counted[0], counted[1], "run"
            shown = core.parse_output_progress(read_tail(log, 4096))   # "42%", "12/48", "Fra:193" in its output
            return (shown[0], shown[1], "run") if shown else (-1, "running", "run")
        if kind == "files":
            # numbered output files (renders, exports): count first..last in the folder
            try:
                names = os.listdir(job.get("log") or "")
            except OSError:
                names = []
            return core.parse_files(names, job.get("pattern") or "", job.get("first") or 1, job.get("last") or 1,
                                    job.get("unit") or "frames")
        if kind == "agent":
            return (100, "finished", "done") if job.get("state") == "done" else (-1, "working", "run")
        if kind == "fg":
            if job.get("state") == "done":
                return 100, "finished", "done"
            if job.get("state") == "fail":
                return 100, "failed", "fail"
            counted = self.counted_output(job, started)
            return (counted[0], counted[1], "run") if counted else (-1, "running (foreground)", "run")
        if kind == "hf":
            return core.parse_hf(read_tail(job.get("log")))
        if kind == "ffmpeg":
            return core.parse_ffmpeg(read_tail(job.get("log")), job.get("total"))
        return core.parse_pct(read_tail(job.get("log")))

    # ------------------------------------------------------------ main loop
    def tick(self):
        try:
            self.update()
        except Exception:
            self.log_error(traceback.format_exc(limit=3).replace("\n", " | "))
        self.root.after(2000, self.tick)

    def update(self):
        now = time.time()
        try:
            open(self.heartbeat, "w").write(time.strftime("%Y-%m-%dT%H:%M:%S"))
        except OSError:
            pass
        for path in sorted(glob.glob(os.path.join(self.jobs, "*.job"))):
            key = os.path.basename(path)[:-4]
            try:
                job = json.load(open(path, encoding="utf-8-sig"))
            except (OSError, ValueError):
                continue
            kind = job.get("kind")
            started = float(job.get("started") or mtime(path) or now)
            if kind == "fg":
                file_age = now - (mtime(path) or now)
                if job.get("state") == "gone" or (job.get("state") != "run" and key not in self.rows):
                    self.drop(key)
                    if file_age > 30:
                        self.remove_file(path)
                    continue
                if key not in self.rows and now - started < core.FG_MIN_S:
                    continue
                if job.get("state") == "run" and core.fg_finished(read_tail(self.transcript_for(job), 400000), job.get("tuid")):
                    self.drop(key)
                    self.remove_file(path)
                    continue
            if key not in self.rows:
                self.new_row(key)
                self.show()   # a new task shows the window (also after it was hidden)
            r = self.rows[key]
            if r.ended:
                continue
            if kind == "agent":
                job["log"] = self.agent_log(key, job)
                if not r.desc and job.get("log"):
                    try:
                        r.desc = json.load(open(job["log"][:-6] + ".meta.json", encoding="utf-8")).get("description")
                    except (OSError, ValueError):
                        pass
            title = r.desc or job.get("name") or key
            if job.get("project"):
                title = "[%s] %s" % (job["project"], title)
            r.title, r.job = title, job

            pct, text, state = self.get_state(job, now, started)
            # activity: its log/output changing, or new files landing in the output folder the watcher found
            # (the newest matching file, not the folder's own time: that only changes when files are added or removed)
            newest = None
            if job.get("out_dir") and job.get("pattern"):
                newest = core.newest_match(folder_entries(job["out_dir"]) or [], job["pattern"])
            log_time = max(mtime(job.get("log")) or started, newest or 0)
            alive = cpu = None
            if job.get("pid"):
                alive = plat.pid_alive(job["pid"])
                cpu = plat.cpu_seconds(job["pid"]) if alive else None
            waiting = False
            if kind == "agent" and now - log_time > 60:
                waiting = core.agent_waiting(read_tail(job.get("log"), 262144))
            res = core.judge(r.watch, job, state, now, started, log_time, cpu, alive, waiting)
            if res["alert"]:
                self.alert(title)
            state = res["state"]
            elapsed = core.fmt_age(now - started)
            if state == "run":
                if pct < 0:
                    self.set_mode(r, "indeterminate")
                else:
                    self.set_mode(r, "determinate")
                    r.bar["value"] = max(0, min(100, pct))
                r.level = res["level"]
                self.set_row_text(r, title, core.status_line(text, pct, state, now - started, res["text"]),
                                  self.COLORS.get(res["level"], "black"))
            else:
                r.ended = now
                self.set_mode(r, "determinate")
                r.bar["value"] = 100
                self.set_row_text(r, title, "%s  \u00b7  %s" % (res["text"] or text, elapsed), self.COLORS.get(state, "black"))
                self.remove_file(path)
        for k in list(self.rows):
            if self.rows[k].ended and now - self.rows[k].ended > 5:
                self.drop(k)
        self.layout(now)

    def drop(self, key):
        r = self.rows.pop(key, None)
        if r:
            r.frame.destroy()

    @staticmethod
    def remove_file(path):
        try:
            os.remove(path)
        except OSError:
            pass

    def layout(self, now):
        root = self.root
        if self.title_reset and now > self.title_reset:
            self.title_reset = None
        live = [r for r in self.rows.values() if not r.ended]
        if not self.title_reset:
            red = sum(r.level == "red" for r in live)
            amber = sum(r.level == "amber" for r in live)
            note = ("%d running" % len(live) + (", %d stalled" % red if red else ", %d quiet" % amber if amber else "")) if live else "idle"
            root.title("Claude Progress Bar  -  " + note)
        n = max(1, len(self.rows))
        max_rows = max(3, (root.winfo_screenheight() - 160) // 52)
        h = 16 + 52 * min(n, max_rows)
        x, y = (root.winfo_x(), root.winfo_y()) if root.winfo_viewable() else self.pos
        root.geometry("560x%d+%d+%d" % (h, x, y))
        if self.rows:
            self.last_active = now
        elif root.winfo_viewable() and now - self.last_active > 6:
            self.hide()   # nothing running: hide but keep listening

    def run(self):
        self.root.mainloop()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=default_data_dir())
    a = ap.parse_args()
    os.makedirs(os.path.join(a.data, "jobs"), exist_ok=True)
    lock = plat.SingleInstance(os.path.join(a.data, "window.lock"))
    if not lock.ok:
        return  # another window is already running
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception as e:
        # no Tk in this Python (Homebrew: brew install python-tk). Say so once, not on every task.
        flag = os.path.join(a.data, "tkinter_missing.notified")
        with open(os.path.join(a.data, "hook_errors.log"), "a", encoding="utf-8") as f:
            f.write("%s window: tkinter is not available (%r). Install it, e.g. brew install python-tk\n" % (time.ctime(), e))
        if not os.path.exists(flag):
            open(flag, "w").write("1")
            plat.notify("Claude Progress Bar needs tkinter", "Install it (Homebrew: brew install python-tk), then it starts with the next task.")
        return
    try:
        os.remove(os.path.join(a.data, "window.heartbeat.launch"))
    except OSError:
        pass
    App(tk, ttk, a.data).run()


if __name__ == "__main__":
    main()
