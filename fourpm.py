#!/usr/bin/env python3
"""
4PM - a small always-on-top task window.

The rules it enforces:
  1. Every day's deadline is 4:00 PM. At 4:00 PM the list flips to the next day.
  2. Anything not ticked at the flip is carried onto the next day's list,
     and it says how many days late it is.
  3. Nothing is ever created for you. No repeats, no templates.
  4. Until you have typed at least one NEW task for the day, a banner sits at
     the top. Carried-over tasks do not clear it.

Run it:                 python3 fourpm.py
Start it at login:      python3 fourpm.py --install
Stop starting at login: python3 fourpm.py --uninstall

Your tasks are saved in a folder called FourPM in your home folder (tasks.json).
"""

import json
import os
import re
import shutil
import socket
import sys
import uuid
from datetime import date, datetime, time, timedelta
from pathlib import Path

# ----------------------------------------------------------------------------
# Settings you can change
# ----------------------------------------------------------------------------
DEADLINE_HOUR = 16       # 16 = 4:00 PM. The day flips at this hour.
NAG_FROM_HOUR = 5        # From 5 AM until the deadline the banner is red and
                         # the window refuses to stay minimised until you
                         # have written a task for today.
APP_DIR = Path.home() / "FourPM"
DATA_FILE = APP_DIR / "tasks.json"
LOCK_PORT = 47816        # only used to stop two copies running at once


# ----------------------------------------------------------------------------
# The rules (no window code in this half - this is the part worth reading)
# ----------------------------------------------------------------------------
def now():
    return datetime.now()


def task_day(moment):
    """The date whose 4 PM deadline is the next one coming.

    Tue 15:59 -> Tuesday.   Tue 16:00 -> Wednesday.   Wed 09:00 -> Wednesday.
    """
    if moment.hour < DEADLINE_HOUR:
        return moment.date()
    return moment.date() + timedelta(days=1)


def deadline_of(day):
    return datetime.combine(day, time(DEADLINE_HOUR))


def new_state(day):
    return {"version": 1, "day": day.isoformat(), "tasks": [], "history": {}}


def roll_over(state, today):
    """Move the list onto `today`. Returns True if anything changed.

    Ticked tasks leave the list and are filed under the day they belonged to.
    Unticked tasks stay on the list. Their "for_day" is never rewritten, which
    is how the window knows how many days late each one is.
    """
    current = date.fromisoformat(state["day"])
    if today == current:
        return False
    if today > current:
        done = [t["text"] for t in state["tasks"] if t["done"]]
        missed = [t["text"] for t in state["tasks"] if not t["done"]]
        if done or missed:
            state["history"][state["day"]] = {"done": done, "missed": missed}
        state["tasks"] = [t for t in state["tasks"] if not t["done"]]
    # (today < current only happens if the laptop clock was moved backwards;
    #  in that case just follow the clock and leave the tasks alone.)
    state["day"] = today.isoformat()
    return True


def is_carried(task, state):
    return task["for_day"] != state["day"]


def days_late(task, state):
    return (date.fromisoformat(state["day"]) - date.fromisoformat(task["for_day"])).days


def needs_writing(state):
    """True until at least one task has been typed for the current day."""
    return not any(t["for_day"] == state["day"] for t in state["tasks"])


def add_task(state, text):
    text = " ".join(text.split())
    if not text:
        return None
    task = {
        "id": uuid.uuid4().hex[:8],
        "text": text,
        "for_day": state["day"],
        "done": False,
        "done_at": None,
    }
    state["tasks"].append(task)
    return task


def ordered(state):
    """Carried-over tasks first (oldest first), then the ones written for today."""
    carried = [t for t in state["tasks"] if is_carried(t, state)]
    fresh = [t for t in state["tasks"] if not is_carried(t, state)]
    carried.sort(key=lambda t: t["for_day"])
    return carried, fresh


# ----------------------------------------------------------------------------
# Saving and loading
# ----------------------------------------------------------------------------
def load():
    today = task_day(now())
    if not DATA_FILE.exists():
        return new_state(today)
    try:
        state = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        state["tasks"], state["day"], state["history"]  # must all be there
        return state
    except (ValueError, KeyError, TypeError):
        # Never throw a damaged file away: set it aside and start clean.
        aside = DATA_FILE.with_name("tasks.damaged-%s.json" % now().strftime("%Y%m%d-%H%M%S"))
        DATA_FILE.replace(aside)
        return new_state(today)


def save(state):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    tmp = DATA_FILE.with_name(DATA_FILE.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, DATA_FILE)   # swap in one step, so a crash can't half-write it


# ----------------------------------------------------------------------------
# Small text helpers
# ----------------------------------------------------------------------------
def fmt_day(d):
    return "%s %d %s" % (d.strftime("%a"), d.day, d.strftime("%b"))   # Tue 6 Oct


def fmt_deadline():
    hour12 = DEADLINE_HOUR % 12 or 12
    return "%d:00 %s" % (hour12, "AM" if DEADLINE_HOUR < 12 else "PM")


def fmt_left(delta):
    minutes = max(0, int(delta.total_seconds() // 60))
    hours, minutes = divmod(minutes, 60)
    return "%dh %02dm left" % (hours, minutes) if hours else "%dm left" % minutes


# ----------------------------------------------------------------------------
# The window
# ----------------------------------------------------------------------------
BG = "#1c1c1e"
PANEL = "#2c2c2e"
TEXT = "#f2f2f7"
MUTED = "#8e8e93"
RED = "#ff453a"
AMBER = "#ffb340"
GREEN = "#32d74b"
PLACEHOLDER = "Type a task, press Enter"


class App:
    def __init__(self):
        import tkinter as tk
        from tkinter import font as tkfont
        self.tk = tk

        self.state = load()
        if roll_over(self.state, task_day(now())):
            save(self.state)

        root = self.root = tk.Tk()
        root.title("4PM")
        root.configure(bg=BG)
        root.attributes("-topmost", True)          # stay above other windows
        root.minsize(260, 220)
        self._place_window()

        family = tkfont.nametofont("TkDefaultFont").actual("family")
        base = 13 if sys.platform == "darwin" else 10
        self.f_text = tkfont.Font(family=family, size=base)
        self.f_done = tkfont.Font(family=family, size=base, overstrike=True)
        self.f_small = tkfont.Font(family=family, size=base - 2)
        self.f_head = tkfont.Font(family=family, size=base - 2, weight="bold")
        self.f_title = tkfont.Font(family=family, size=base + 7, weight="bold")
        self.f_box = tkfont.Font(family=family, size=base + 4)

        root.columnconfigure(0, weight=1)
        root.rowconfigure(3, weight=1)

        # Row 0: which day this list is for, and how long is left
        head = tk.Frame(root, bg=BG)
        head.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))
        self.lbl_day = tk.Label(head, bg=BG, fg=TEXT, font=self.f_title, anchor="w")
        self.lbl_day.pack(fill="x")
        self.lbl_left = tk.Label(head, bg=BG, fg=MUTED, font=self.f_small, anchor="w")
        self.lbl_left.pack(fill="x")

        # Row 1: the banner (only on screen while nothing is written for the day)
        self.banner = tk.Label(root, fg="#000000", font=self.f_head, anchor="w",
                               justify="left", padx=12, pady=8)
        self.banner.grid(row=1, column=0, sticky="ew", padx=14, pady=(2, 6))

        # Row 2: where you type
        self.entry = tk.Entry(root, bg=PANEL, fg=MUTED, insertbackground=TEXT,
                              relief="flat", font=self.f_text,
                              highlightthickness=1, highlightbackground=PANEL,
                              highlightcolor=MUTED)
        self.entry.grid(row=2, column=0, sticky="ew", padx=14, pady=(2, 8), ipady=6)
        self.entry.insert(0, PLACEHOLDER)
        self._placeholder_on = True
        self.entry.bind("<FocusIn>", self._entry_focus_in)
        self.entry.bind("<FocusOut>", self._entry_focus_out)
        self.entry.bind("<Return>", self._entry_submit)

        # Row 3: the list (scrolls if it gets long)
        wrap = tk.Frame(root, bg=BG)
        wrap.grid(row=3, column=0, sticky="nsew", padx=(14, 4))
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0, bd=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scroll = tk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        self.scroll.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.rows = tk.Frame(self.canvas, bg=BG)
        self.rows_id = self.canvas.create_window(0, 0, window=self.rows, anchor="nw")
        self.rows.bind("<Configure>", self._fit_scroll)
        self.canvas.bind("<Configure>", self._fit_width)
        for event in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            root.bind_all(event, self._wheel)

        # Row 4: count
        self.lbl_count = tk.Label(root, bg=BG, fg=MUTED, font=self.f_small, anchor="w")
        self.lbl_count.grid(row=4, column=0, sticky="ew", padx=14, pady=(6, 10))

        self.text_labels = []
        self._geometry_job = None
        root.bind("<Configure>", self._remember_window)
        root.protocol("WM_DELETE_WINDOW", self._quit)

        self.render()
        self.tick()

    # ---- the clock ---------------------------------------------------------
    def sync_day(self):
        """Flip the list if 4 PM has passed since we last looked."""
        if roll_over(self.state, task_day(now())):
            save(self.state)
            return True
        return False

    def tick(self):
        flipped = self.sync_day()
        if flipped:
            self.render()
        else:
            self.render_header()
        if self._nagging() and self.root.state() == "iconic":
            self.root.deiconify()       # not allowed to hide until you've written
            self.root.lift()
        self.root.after(15000, self.tick)   # look again in 15 seconds

    def _nagging(self):
        t = now()
        is_today = date.fromisoformat(self.state["day"]) == t.date()
        return needs_writing(self.state) and is_today and t.hour >= NAG_FROM_HOUR

    # ---- drawing -----------------------------------------------------------
    def render_header(self):
        t = now()
        day = date.fromisoformat(self.state["day"])
        is_today = day == t.date()
        word = "Today" if is_today else "Tomorrow"
        self.lbl_day.config(text="%s  ·  %s" % (word, fmt_day(day)))

        left = deadline_of(day) - t
        urgent = left < timedelta(hours=1)
        self.lbl_left.config(
            text="Deadline %s%s  ·  %s" % (fmt_deadline(), "" if is_today else " tomorrow", fmt_left(left)),
            fg=RED if urgent else MUTED)

        if needs_writing(self.state):
            carried = sum(1 for t_ in self.state["tasks"] if not t_["done"])
            if self._nagging():
                msg = "WRITE TODAY'S TASKS"
                if carried:
                    msg += "\n%d carried over. They don't count." % carried
                self.banner.config(text=msg, bg=RED, fg="#ffffff")
            else:
                msg = "Nothing written for %s yet" % word.lower()
                self.banner.config(text=msg, bg=AMBER, fg="#000000")
            self.banner.grid()
        else:
            self.banner.grid_remove()

        total = len(self.state["tasks"])
        done = sum(1 for t_ in self.state["tasks"] if t_["done"])
        self.lbl_count.config(text="%d of %d done" % (done, total) if total else "No tasks yet")

    def render(self):
        tk = self.tk
        self.render_header()
        for child in self.rows.winfo_children():
            child.destroy()
        self.text_labels = []

        carried, fresh = ordered(self.state)
        is_today = date.fromisoformat(self.state["day"]) == now().date()

        if carried:
            self._heading("CARRIED OVER  (%d)" % len(carried), RED)
            for task in carried:
                self._row(task, carried=True)
        if fresh:
            self._heading("WRITTEN FOR %s" % ("TODAY" if is_today else "TOMORROW"), MUTED,
                          gap=bool(carried))
            for task in fresh:
                self._row(task, carried=False)
        if not carried and not fresh:
            tk.Label(self.rows, text="Nothing here.", bg=BG, fg=MUTED,
                     font=self.f_small, anchor="w").pack(fill="x", pady=8)
        self.root.after_idle(lambda: self._fit_width(None))

    def _heading(self, text, colour, gap=False):
        self.tk.Label(self.rows, text=text, bg=BG, fg=colour, font=self.f_head,
                      anchor="w").pack(fill="x", pady=(14 if gap else 2, 4))

    def _row(self, task, carried):
        tk = self.tk
        row = tk.Frame(self.rows, bg=BG)
        row.pack(fill="x", pady=3)
        row.columnconfigure(1, weight=1)

        box = tk.Label(row, text="☑" if task["done"] else "☐", bg=BG,
                       fg=GREEN if task["done"] else (RED if carried else TEXT),
                       font=self.f_box, cursor="hand2")
        box.grid(row=0, column=0, sticky="nw", padx=(0, 8))

        label = tk.Label(row, text=task["text"], bg=BG,
                         fg=MUTED if task["done"] else TEXT,
                         font=self.f_done if task["done"] else self.f_text,
                         anchor="w", justify="left", cursor="hand2")
        label.grid(row=0, column=1, sticky="ew", pady=(2, 0))
        self.text_labels.append(label)

        cross = tk.Label(row, text="✕", bg=BG, fg="#48484a", font=self.f_small, cursor="hand2")
        cross.grid(row=0, column=2, sticky="ne", padx=(6, 6), pady=(3, 0))
        cross.bind("<Enter>", lambda e: cross.config(fg=RED))
        cross.bind("<Leave>", lambda e: cross.config(fg="#48484a"))
        cross.bind("<Button-1>", lambda e: self.delete(task["id"]))

        if carried:
            n = days_late(task, self.state)
            was_for = date.fromisoformat(task["for_day"])
            note = "was for %s  ·  %d day%s late" % (fmt_day(was_for), n, "" if n == 1 else "s")
            sub = tk.Label(row, text=note, bg=BG, fg=MUTED if task["done"] else RED,
                           font=self.f_small, anchor="w")
            sub.grid(row=1, column=1, sticky="ew")

        for widget in (box, label):
            widget.bind("<Button-1>", lambda e: self.toggle(task["id"]))

    # ---- things you can do -------------------------------------------------
    def _find(self, task_id):
        for task in self.state["tasks"]:
            if task["id"] == task_id:
                return task
        return None

    def add(self, text):
        self.sync_day()                      # so a task typed at 4:00:05 PM lands on tomorrow
        if add_task(self.state, text):
            save(self.state)
        self.render()

    def toggle(self, task_id):
        self.sync_day()
        task = self._find(task_id)
        if task:
            task["done"] = not task["done"]
            task["done_at"] = now().isoformat(timespec="seconds") if task["done"] else None
            save(self.state)
        self.render()

    def delete(self, task_id, ask=True):
        self.sync_day()
        task = self._find(task_id)
        if not task:
            return self.render()
        if ask and is_carried(task, self.state) and not task["done"]:
            from tkinter import messagebox
            if not messagebox.askyesno(
                    "Drop it?", "This one is carried over and not done:\n\n%s\n\n"
                    "Delete it without doing it?" % task["text"], parent=self.root):
                return
        self.state["tasks"].remove(task)
        save(self.state)
        self.render()

    # ---- typing box --------------------------------------------------------
    def _entry_focus_in(self, _event):
        if self._placeholder_on:
            self.entry.delete(0, "end")
            self.entry.config(fg=TEXT)
            self._placeholder_on = False

    def _entry_focus_out(self, _event):
        if not self.entry.get().strip():
            self.entry.delete(0, "end")
            self.entry.insert(0, PLACEHOLDER)
            self.entry.config(fg=MUTED)
            self._placeholder_on = True

    def _entry_submit(self, _event):
        if self._placeholder_on:
            return
        text = self.entry.get()
        self.entry.delete(0, "end")
        self.add(text)

    # ---- scrolling and sizing ----------------------------------------------
    def _fit_scroll(self, _event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        if self.rows.winfo_reqheight() > self.canvas.winfo_height():
            self.scroll.grid()
        else:
            self.scroll.grid_remove()
            self.canvas.yview_moveto(0)

    def _fit_width(self, _event):
        width = self.canvas.winfo_width()
        self.canvas.itemconfigure(self.rows_id, width=width)
        for label in self.text_labels:
            label.config(wraplength=max(80, width - 70))   # long tasks wrap, not clip
        self._fit_scroll(None)

    def _wheel(self, event):
        if self.rows.winfo_reqheight() <= self.canvas.winfo_height():
            return
        if getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0:
            self.canvas.yview_scroll(-1, "units")
        else:
            self.canvas.yview_scroll(1, "units")

    # ---- remembering where you put the window ------------------------------
    def _place_window(self):
        root = self.root
        width, height = 330, 470
        x = root.winfo_screenwidth() - width - 24      # default: top-right corner
        y = 48
        saved = re.fullmatch(r"(\d+)x(\d+)\+(\d+)\+(\d+)", self.state.get("window") or "")
        if saved:
            w, h, sx, sy = (int(n) for n in saved.groups())
            if sx < root.winfo_screenwidth() - 60 and sy < root.winfo_screenheight() - 60:
                width, height, x, y = w, h, sx, sy     # only if it is still on screen
        root.geometry("%dx%d+%d+%d" % (width, height, x, y))

    def _remember_window(self, event):
        if event.widget is not self.root:
            return
        if self._geometry_job:
            self.root.after_cancel(self._geometry_job)
        self._geometry_job = self.root.after(1000, self._save_window)

    def _save_window(self):
        self._geometry_job = None
        if self.root.state() == "normal":
            self.state["window"] = self.root.geometry()
            save(self.state)

    def _quit(self):
        if self.root.state() == "normal":
            self.state["window"] = self.root.geometry()
        save(self.state)
        self.root.destroy()


# ----------------------------------------------------------------------------
# Starting at login
# ----------------------------------------------------------------------------
MAC_PLIST = Path.home() / "Library" / "LaunchAgents" / "com.fourpm.tasks.plist"
LINUX_DESKTOP = Path.home() / ".config" / "autostart" / "fourpm.desktop"
WIN_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def install():
    """Make the window open by itself every time you log in."""
    APP_DIR.mkdir(parents=True, exist_ok=True)
    here = Path(__file__).resolve()
    target = APP_DIR / "fourpm.py"
    if here != target:
        shutil.copyfile(here, target)      # the login copy lives next to your tasks

    if sys.platform == "darwin":
        import plistlib
        MAC_PLIST.parent.mkdir(parents=True, exist_ok=True)
        with open(MAC_PLIST, "wb") as fh:
            plistlib.dump({
                "Label": "com.fourpm.tasks",
                "ProgramArguments": [sys.executable, str(target)],
                "RunAtLoad": True,
            }, fh)
        where = str(MAC_PLIST)
    elif sys.platform.startswith("win"):
        import winreg
        pythonw = Path(sys.executable).with_name("pythonw.exe")    # no black console window
        runner = pythonw if pythonw.exists() else Path(sys.executable)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WIN_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "FourPM", 0, winreg.REG_SZ, '"%s" "%s"' % (runner, target))
        where = "your Windows startup list"
    else:
        LINUX_DESKTOP.parent.mkdir(parents=True, exist_ok=True)
        LINUX_DESKTOP.write_text(
            "[Desktop Entry]\nType=Application\nName=4PM\nExec=%s %s\n" % (sys.executable, target),
            encoding="utf-8")
        where = str(LINUX_DESKTOP)

    print("Done. 4PM will open every time you log in.")
    print("  Program copy: %s" % target)
    print("  Login entry:  %s" % where)
    print("To open it right now:  %s %s" % (Path(sys.executable).name, target))


def uninstall():
    if sys.platform == "darwin":
        MAC_PLIST.unlink(missing_ok=True)
    elif sys.platform.startswith("win"):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WIN_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, "FourPM")
        except FileNotFoundError:
            pass
    else:
        LINUX_DESKTOP.unlink(missing_ok=True)
    print("4PM will no longer open at login. Your tasks are untouched in %s" % APP_DIR)


def single_copy_lock():
    """Hold a private port for as long as we run; a second copy can't take it."""
    lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock.bind(("127.0.0.1", LOCK_PORT))
    except OSError:
        return None
    return lock


def main():
    if "--install" in sys.argv:
        return install()
    if "--uninstall" in sys.argv:
        return uninstall()
    import tkinter
    if tkinter.TkVersion < 8.6:
        print("This Python comes with an old window toolkit (Tk %s), which draws badly.\n"
              "Install Python from python.org and run this again with that one." % tkinter.TkVersion)
        return
    lock = single_copy_lock()
    if lock is None:
        print("4PM is already open.")
        return
    app = App()
    app.root.mainloop()
    lock.close()


if __name__ == "__main__":
    main()
