from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Input, Static, TextArea, OptionList
from textual.widgets.option_list import Option
from textual.containers import Horizontal, VerticalScroll
from textual.suggester import Suggester
from textual.screen import Screen
from textual.theme import Theme
from datetime import datetime, timedelta, date
import hashlib
import json
import os
import re
import random
import colorsys
import sys
import shutil
import subprocess
import io
import wave
import struct
import math

# glyphs used for the "decrypt" scramble effect and title glitch
SCRAMBLE_GLYPHS = "!@#$%&*+=/\\<>?▓▒░01x"

# short synthesized "technical" blips per event: lists of (frequency_hz, ms).
# ascending tones read as a sci-fi UI confirm rather than a notification chime.
TECH_TONES = {
    "complete": [(784, 70), (1175, 90)],            # G5 -> D6, satisfying confirm
    "win":      [(784, 80), (988, 80), (1319, 150)],  # G5-B5-E6 arpeggio, bigger reward
    "start":    [(659, 60)],                          # E5 single blip
    "toggle":   [(1047, 40)],                         # C6 tick
}

# our signature Cyberpunk 2077 look, registered alongside Textual's built-in
# themes (dracula, gruvbox, catppuccin-*, nord, rose-pine, tokyo-night, ...)
CP2077_THEME = Theme(
    name="cp2077",
    primary="#f3e600",     # neon yellow
    secondary="#00b8cc",   # dim cyan
    accent="#00e5ff",      # bright cyan
    foreground="#e8fbff",
    background="#0b0d0f",
    surface="#11171a",
    panel="#11171a",
    success="#00e5ff",
    warning="#f3e600",
    error="#ff3b3b",
    dark=True,
)
DEFAULT_THEME = "cp2077"

# ---------- STORAGE ----------
DATA_DIR = os.path.join(os.path.expanduser("~"), "TodoApp")
os.makedirs(DATA_DIR, exist_ok=True)
DATA_FILE = os.path.join(DATA_DIR, "data.json")
REPORT_FILE = os.path.join(DATA_DIR, "completed_report.txt")

# How long a completed task stays in the main view before being archived.

ARCHIVE_AFTER = timedelta(hours=24)

# ---------- BIG TIMER FONT (3 rows tall, seven-segment) ----------
BIG = {
    "0": [" _ ", "| |", "|_|"],
    "1": ["   ", "  |", "  |"],
    "2": [" _ ", " _|", "|_ "],
    "3": [" _ ", " _|", " _|"],
    "4": ["   ", "|_|", "  |"],
    "5": [" _ ", "|_ ", " _|"],
    "6": [" _ ", "|_ ", "|_|"],
    "7": [" _ ", "  |", "  |"],
    "8": [" _ ", "|_|", "|_|"],
    "9": [" _ ", "|_|", " _|"],
    ":": ["   ", " ° ", " ° "],
    " ": ["   ", "   ", "   "],
}


# ---------- TASK ----------
class Task:
    def __init__(self, id, text, path=None, due=None):
        self.id = id
        self.text = text
        self.path = path or []
        self.due = due
        self.completed = False
        self.completed_at = None

    def to_dict(self):
        return {
            "id": self.id,
            "text": self.text,
            "path": self.path,
            "due": self.due.isoformat() if self.due else None,
            "completed": self.completed,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None
        }

    @staticmethod
    def from_dict(data):
        t = Task(data["id"], data["text"], data.get("path"))
        t.completed = data.get("completed", False)
        if data.get("due"):
            t.due = date.fromisoformat(data["due"])
        if data.get("completed_at"):
            t.completed_at = datetime.fromisoformat(data["completed_at"])
        return t


# ---------- AUTOCOMPLETE ----------
class InputSuggester(Suggester):
    """Completes /commands at the start of the line and #hashtags on the last token."""

    def __init__(self, app):
        super().__init__(use_cache=False, case_sensitive=True)
        self._app = app

    async def get_suggestion(self, value):
        if not value:
            return None

        # /command completion (only while the line is a single bare command)
        if value.startswith("/") and " " not in value:
            low = value.lower()
            for c in self._app.COMMANDS:
                if c.lower().startswith(low) and c.lower() != low:
                    return value + c[len(value):]

        # #hashtag completion on the final whitespace-separated token
        cut = value.rfind(" ")
        last = value[cut + 1:]
        if last.startswith("#") and len(last) > 1:
            low = last.lower()
            for tag in sorted(self._app.all_tags()):
                if tag.lower().startswith(low) and tag.lower() != low:
                    return value + tag[len(last):]

        # @due-date completion on the final token (@ alone suggests @today)
        if last.startswith("@"):
            word = last[1:].lower()
            prefix = value[:cut + 1]
            for opt in ("today", "tomorrow", "mon", "tue", "wed", "thu",
                        "fri", "sat", "sun"):
                if opt.startswith(word) and opt != word:
                    return prefix + "@" + opt

        # folder completion: as you type a project path, suggest a matching
        # existing folder — one segment at a time. (Not in command lines.)
        if last and not value.startswith("/") and not last.startswith(("@", "#")):
            prefix = value[:cut + 1]
            folders = self._app.folder_tree()
            if "/" in last:
                # typing a sub-folder: complete the segment after the last "/"
                head, _, partial = last.rpartition("/")
                parent = tuple(s.strip().title() for s in head.split("/") if s.strip())
                for child in sorted(folders.get(parent, set())):
                    if child.lower().startswith(partial.lower()) and child.lower() != partial.lower():
                        return prefix + head + "/" + child
            else:
                # first segment: complete a top-level folder, ending in "/"
                for child in sorted(folders.get((), set())):
                    if child.lower().startswith(last.lower()) and child.lower() != last.lower():
                        return prefix + child + "/"

        return None


# ---------- INPUT WIDGET ----------
class TabInput(Input):
    """An Input where Tab accepts the autocomplete suggestion (just like →)."""

    async def _on_key(self, event):
        if (
            event.key == "tab"
            and self.cursor_at_end
            and self._suggestion
            and self._suggestion != self.value
        ):
            self.value = self._suggestion
            self.cursor_position = len(self.value)
            event.stop()
            event.prevent_default()
            return
        await super()._on_key(event)


# ---------- BOOT SEQUENCE ----------
class BootScreen(Screen):
    """A throwaway startup log that types itself out, then drops into the app."""

    LINES = [
        "> JACKING IN ...",
        "> NETRUNNER OS  v2.077",
        "> MOUNTING ~/TodoApp ........... OK",
        "> BREACHING ICE [{n}] .......... OK",
        "> ACCESS GRANTED — WELCOME, CHOOM",
    ]

    def __init__(self, active):
        super().__init__()
        self.lines = [ln.format(n=active) for ln in self.LINES]
        self._i = 0
        self._timer = None
        self._done = False

    def compose(self) -> ComposeResult:
        self.log_view = Static("", id="boot")
        yield self.log_view

    def on_mount(self):
        self._timer = self.set_interval(0.08, self._tick)

    def _tick(self):
        n = len(self.lines)
        if self._i <= n:
            self.log_view.update("\n".join(self.lines[:self._i]) + " ▮")
            self._i += 1
        elif self._i >= n + 4:        # brief hold on the final frame
            self._finish()
        else:
            self._i += 1

    def on_key(self, event):
        self._finish()                # any key skips the intro

    def _finish(self):
        if self._done:
            return
        self._done = True
        if self._timer:
            self._timer.stop()
        self.app.pop_screen()
        try:
            self.app.query_one("#cmd").focus()
        except Exception:
            pass


# ---------- NOTES ----------
class NotesScreen(Screen):
    """A full-page editor for one folder's note (Notion-style page)."""

    BINDINGS = [
        ("escape", "close", "save & close"),
        ("ctrl+s", "save", "save"),
    ]

    def __init__(self, path):
        super().__init__()
        self.path = path

    def compose(self) -> ComposeResult:
        yield Static(f"  NOTE · {self.path}", id="note-title")
        yield TextArea(self.app.notes.get(self.path, ""), id="note-edit")
        yield Static("  Ctrl+S save  ·  Esc save & close", id="note-help")

    def on_mount(self):
        self.query_one("#note-edit").focus()

    def _persist(self):
        text = self.query_one("#note-edit").text.strip()
        if text:
            self.app.notes[self.path] = text
        else:
            self.app.notes.pop(self.path, None)  # empty note = no note
        self.app.save_data()
        self.app.refresh_all()

    def action_save(self):
        self._persist()
        self.app.notify(f"Note saved · {self.path}")

    def action_close(self):
        self._persist()
        self.app.pop_screen()


class NotesBrowserScreen(Screen):
    """Search folder paths and open their notes."""

    BINDINGS = [("escape", "close", "close")]

    def compose(self) -> ComposeResult:
        yield Static("  NOTES — search a folder path, Enter to open", id="notes-title")
        yield Input(placeholder="search… (or type a new path and press Enter to create)",
                    id="notes-search")
        yield OptionList(id="notes-list")
        yield Static("  ↑↓ select · Enter open · Esc back", id="notes-help")

    def on_mount(self):
        self._populate("")
        self.query_one("#notes-search").focus()

    def on_screen_resume(self):
        # refresh after returning from the editor (a note may have changed)
        self._populate(self.query_one("#notes-search").value)

    def _populate(self, query):
        ol = self.query_one("#notes-list")
        ol.clear_options()
        q = query.strip().lower()
        shown = 0
        for path, text in sorted(self.app.notes.items()):
            if q in path.lower():
                snippet = " ".join(text.split())[:60]
                ol.add_option(Option(f"{path}   —   {snippet}", id=path))
                shown += 1
        if shown == 0:
            msg = "(type a path and press Enter to create a note)" if not self.app.notes \
                else "(no match — type a path and press Enter to create)"
            ol.add_option(Option(msg, id="__none__"))

    def on_input_changed(self, event: Input.Changed):
        self._populate(event.value)

    def on_input_submitted(self, event: Input.Submitted):
        path = self.app._normalize_note_path(event.value)
        if path:
            self.app.push_screen(NotesScreen(path))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected):
        oid = event.option.id
        if oid and oid != "__none__":
            self.app.push_screen(NotesScreen(oid))

    def action_close(self):
        self.app.pop_screen()


# ---------- APP ----------
class TodoApp(App):
    TITLE = "TODO//2077"
    SUB_TITLE = "night city task grid"

    COMMANDS = [
        "/help", "/current", "/done", "/edit", "/due", "/delete", "/filter",
        "/completed", "/back", "/clear all", "/export", "/sound", "/theme",
        "/notes",
    ]

    # All colors come from the active theme's variables, so /theme reskins the
    # whole UI at once (works for cp2077 and every built-in Textual theme).
    CSS = """
    Screen { layout: vertical; background: $background; color: $foreground; }

    /* title bar tinted with the theme's primary; footer on its panel color */
    Header { background: $primary; color: $background; text-style: bold; }
    Footer { background: $panel; color: $secondary; }

    /* main panes take all the flexible space, pushing the rest to the bottom */
    #main { height: 1fr; }

    /* scrollable wrappers hold the border/width; inner Static grows to fit so
       the wrapper can scroll when there are more tasks than fit on screen */
    #graph-scroll {
        width: 65%; height: 1fr;
        border: round $primary;
        border-title-color: $primary; border-title-align: left;
        scrollbar-color: $primary; scrollbar-background: $surface;
    }
    #tasks-scroll {
        width: 35%; height: 1fr;
        border: round $accent;
        border-title-color: $accent; border-title-align: left;
        scrollbar-color: $accent; scrollbar-background: $surface;
    }
    #graph, #tasks { padding: 1; height: auto; }

    /* status bar: big pomodoro timer + loading bar + stats (not docked) */
    #status {
        height: 7;
        border-top: heavy $primary;
        padding: 0 1;
        color: $foreground;
    }

    /* faded contextual hint line, sits just above the input */
    #hint { height: 1; padding: 0 1; color: $secondary; }

    /* input sits just above the footer in normal flow */
    #cmd {
        height: 3;
        border: tall $primary;
        background: $panel;
        color: $foreground;
    }
    #cmd:focus { border: tall $accent; }

    /* boot sequence overlay */
    BootScreen { background: $background; }
    #boot { padding: 2 4; color: $primary; text-style: bold; }

    /* notes editor + browser pages */
    NotesScreen, NotesBrowserScreen { background: $background; }
    #note-title, #notes-title {
        height: 1; padding: 0 1;
        background: $primary; color: $background; text-style: bold;
    }
    #note-help, #notes-help { height: 1; padding: 0 1; color: $secondary; }
    #note-edit { height: 1fr; border: round $accent; }
    #notes-search { border: tall $primary; background: $panel; color: $foreground; }
    #notes-list { height: 1fr; border: round $accent; }
    """

    def __init__(self):
        super().__init__()
        self.tasks = []
        self.archived_tasks = []
        self.task_id = 1
        self.show_completed = False
        self.current_task_id = None
        self.timer_end = None
        self.timer_total = None
        self.editing_task_id = None
        self.filter_tag = None
        self.filter_due = None
        self.notes = {}  # folder-path string -> free-form note text
        # animation / telemetry state
        self.start_time = datetime.now()
        self.blink = False
        self._tick_count = 0
        self._title_base = self.TITLE
        self._reveal = None
        self._reveal_timer = None
        # selected color theme (applied in on_mount once the app is running)
        self._theme_name = DEFAULT_THEME
        # sound: little dopamine hits on "win" events (cross-platform, no deps)
        self.sound_on = True
        self._wav_ref = None                             # keeps win32 buffer alive
        self._afplay = shutil.which("afplay")            # macOS
        self._linux_player = (                           # linux best-effort
            shutil.which("canberra-gtk-play") or shutil.which("paplay")
        )

    # ---------- UI ----------
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)

        with Horizontal(id="main"):
            with VerticalScroll(id="graph-scroll"):
                self.graph_view = Static("", id="graph")
                yield self.graph_view
            with VerticalScroll(id="tasks-scroll"):
                self.task_view = Static("", id="tasks")
                yield self.task_view

        self.status_bar = Static("", id="status")
        yield self.status_bar

        self.hint_bar = Static(self.hint_for(""), id="hint")
        yield self.hint_bar

        self.input_box = TabInput(
            placeholder="user@2077:~$  enter task or /command …",
            suggester=InputSuggester(self),
            id="cmd"
        )
        yield self.input_box
        yield Footer()

    def on_mount(self):
        self.register_theme(CP2077_THEME)
        self.query_one("#graph-scroll").border_title = "[ PROJECTS ]"
        self.query_one("#tasks-scroll").border_title = "[ TASKS ]"
        self.load_data()
        # apply the saved theme (fall back to default if it's unknown)
        self.theme = self._theme_name if self._theme_name in self.available_themes else DEFAULT_THEME
        self.refresh_all()
        self.set_interval(30, self.cleanup_completed_tasks)
        self.set_interval(1, self.update_timer)
        self.set_interval(0.5, self._heartbeat)     # blink + telemetry
        self.set_interval(3.0, self._glitch_title)  # occasional title glitch
        active = len([t for t in self.tasks if not t.completed])
        self.push_screen(BootScreen(active))        # typed startup log

    # ---------- THEME PALETTE (for dynamic markup colors) ----------
    def _palette(self):
        # the active theme object; its .primary/.accent/.error/... are hex strings
        try:
            return self.get_theme(self.theme)
        except Exception:
            return self.get_theme(DEFAULT_THEME)

    def _muted(self):
        return self._palette().secondary

    # ---------- LIVE TELEMETRY / EFFECTS ----------
    def _heartbeat(self):
        # cheap always-on tick: drives the blinking colon, REC dot, shimmer
        self.blink = not self.blink
        self._tick_count += 1
        self.status_bar.update(self.render_big_timer())

    def _uptime_str(self):
        secs = int((datetime.now() - self.start_time).total_seconds())
        h, rem = divmod(secs, 3600)
        m, s = divmod(rem, 60)
        return f"{h:02}:{m:02}:{s:02}"

    def _garble(self, s):
        sub = {"O": "0", "S": "5", "T": "7", "A": "4", "E": "3", "I": "1"}
        out = []
        for c in s:
            if c.isalpha() and random.random() < 0.5:
                out.append(sub.get(c.upper(), random.choice("#%@&$")))
            else:
                out.append(c)
        return "".join(out)

    def _glitch_title(self):
        # flicker the header title for a fraction of a second, then snap back
        self.title = self._garble(self._title_base)
        self.set_timer(0.15, lambda: setattr(self, "title", self._title_base))

    def _start_reveal(self, task):
        self._reveal = {"id": task.id, "n": 0, "len": len(task.text)}
        if self._reveal_timer:
            self._reveal_timer.stop()
        self._reveal_timer = self.set_interval(0.03, self._reveal_tick)

    def _reveal_tick(self):
        r = self._reveal
        if not r:
            return
        r["n"] += 1
        if r["n"] >= r["len"]:
            self._reveal = None
            if self._reveal_timer:
                self._reveal_timer.stop()
                self._reveal_timer = None
        self.refresh_all()

    def _display_text(self, t):
        # real text, unless this task is mid-"decrypt" — then scramble the tail
        r = self._reveal
        if not r or r["id"] != t.id:
            return t.text
        n = r["n"]
        tail = "".join(
            c if c == " " else random.choice(SCRAMBLE_GLYPHS) for c in t.text[n:]
        )
        return t.text[:n] + tail

    # ---------- SOUND ----------
    @staticmethod
    def _synth_wav(tones, volume=0.3, rate=22050):
        """Build a small mono 16-bit WAV (in memory) from (freq, ms) tones,
        with a short fade on each tone so there are no clicks."""
        fade = int(rate * 0.005)
        frames = bytearray()
        for freq, ms in tones:
            n = int(rate * ms / 1000)
            for i in range(n):
                if i < fade:
                    env = i / fade
                elif i > n - fade:
                    env = max(0.0, (n - i) / fade)
                else:
                    env = 1.0
                sample = int(volume * env * 32767 * math.sin(2 * math.pi * freq * i / rate))
                frames += struct.pack("<h", sample)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(bytes(frames))
        return buf.getvalue()

    def _play(self, event):
        """Fire a short system sound for a 'win' event. Non-blocking, best-effort,
        cross-platform; silently does nothing if no audio backend is available."""
        if not self.sound_on:
            return
        try:
            if sys.platform == "darwin" and self._afplay:
                name = {"complete": "Glass", "win": "Hero",
                        "start": "Bottle", "toggle": "Pop"}.get(event, "Pop")
                path = f"/System/Library/Sounds/{name}.aiff"
                if os.path.exists(path):
                    subprocess.Popen(
                        [self._afplay, "-v", "0.4", path],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
            elif sys.platform == "win32":
                import winsound
                data = self._synth_wav(TECH_TONES.get(event, TECH_TONES["toggle"]))
                self._wav_ref = data  # keep buffer alive during async playback
                winsound.PlaySound(data, winsound.SND_MEMORY | winsound.SND_ASYNC)
            elif self._linux_player:
                if self._linux_player.endswith("canberra-gtk-play"):
                    cid = {"complete": "complete", "win": "complete",
                           "start": "message", "toggle": "bell"}.get(event, "bell")
                    cmd = [self._linux_player, "-i", cid]
                else:  # paplay + freedesktop sound theme
                    fname = {"complete": "complete", "win": "complete",
                             "start": "message", "toggle": "bell"}.get(event, "bell")
                    sound = f"/usr/share/sounds/freedesktop/stereo/{fname}.oga"
                    if not os.path.exists(sound):
                        return
                    cmd = [self._linux_player, sound]
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        except Exception:
            pass

    # ---------- TIMER ----------
    def update_timer(self):
        if not self.timer_end:
            return
        if datetime.now() >= self.timer_end:
            self.timer_end = None
            self.timer_total = None
            self.current_task_id = None
            self.notify("⏱ Timer complete")
            self._play("win")
        self.refresh_all()

    def format_timer(self):
        if not self.timer_end:
            return "00:00"
        sec = max(0, int((self.timer_end - datetime.now()).total_seconds()))
        m, s = divmod(sec, 60)
        return f"{m:02}:{s:02}"

    def timer_progress(self):
        # Returns (remaining_sec, total_sec, fraction_elapsed) or None when idle.
        if not self.timer_end or not self.timer_total:
            return None
        remaining = max(0.0, (self.timer_end - datetime.now()).total_seconds())
        total = self.timer_total
        frac = 1 - remaining / total if total else 1.0
        return remaining, total, max(0.0, min(1.0, frac))

    def render_big_timer(self):
        txt = self.format_timer()
        prog = self.timer_progress()
        p = self._palette()

        if prog:
            remaining, total, frac = prog
            rem_frac = remaining / total if total else 0
            color = p.success if rem_frac > 0.5 else (p.warning if rem_frac > 0.2 else p.error)
        else:
            frac = 0.0
            color = p.primary

        # blink the colon once per heartbeat while a timer is running
        if prog and not self.blink:
            txt = txt.replace(":", " ")

        rows = ["", "", ""]
        for ch in txt:
            glyph = BIG.get(ch, BIG[" "])
            for r in range(3):
                rows[r] += glyph[r] + " "
        big = "\n".join(f"[{color}]{row}[/]" for row in rows)

        # Full-width loading bar with a single bright "scanning" cell drifting across.
        bar_w = self.status_bar.size.width - 2
        if bar_w < 10:
            bar_w = 40
        filled = int(round(frac * bar_w))
        shimmer = self._tick_count % bar_w
        cells = []
        for i in range(bar_w):
            ch = "█" if i < filled else "░"
            cells.append(f"[{p.foreground}]{ch}[/]" if i == shimmer else ch)
        bar = "".join(cells)

        # telemetry line: pulsing REC dot, uptime, live buffer count
        rec = f"[{p.error}]●[/]" if self.blink else "[#444444]●[/]"
        active = len([t for t in self.tasks if not t.completed])
        telem = (
            f"{rec} [bold {p.error}]REC[/]   "
            f"[{p.secondary}]uptime {self._uptime_str()}[/]   "
            f"[{p.secondary}]buffer: {active}[/]"
        )

        if prog:
            info = f"[{color}]{int(frac * 100)}% elapsed[/]   [bold]{self.get_stats()}[/]"
        else:
            info = f"[bold]{self.get_stats()}[/]   [dim]· /current <n> <min> to start a timer[/]"

        return f"{big}\n[{color}]{bar}[/]\n{telem}\n{info}"

    # ---------- STATS ----------
    def get_stats(self):
        now = datetime.now()
        today = now.date()
        week_start = today - timedelta(days=now.weekday())

        all_tasks = self.archived_tasks + [t for t in self.tasks if t.completed]

        day = sum(1 for t in all_tasks if t.completed_at and t.completed_at.date() == today)

        week = sum(
            1 for t in all_tasks
            if t.completed_at and week_start <= t.completed_at.date() <= today
        )

        month = sum(
            1 for t in all_tasks
            if t.completed_at and t.completed_at.month == now.month and t.completed_at.year == now.year
        )

        year = sum(
            1 for t in all_tasks
            if t.completed_at and t.completed_at.year == now.year
        )

        return f"Today {day}  ·  Week {week}  ·  Month {month}  ·  Year {year}  ·  Total {len(all_tasks)}"

    # ---------- SAVE / LOAD ----------
    def save_data(self):
        data = {
            "tasks": [t.to_dict() for t in self.tasks],
            "archived": [t.to_dict() for t in self.archived_tasks],
            "task_id": self.task_id,
            "sound_on": self.sound_on,
            "theme": self.theme,
            "notes": self.notes,
        }
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, DATA_FILE)

    def load_data(self):
        if not os.path.exists(DATA_FILE):
            return

        try:
            with open(DATA_FILE, "r") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            self.notify(f"Could not load saved data: {e}")
            return

        self.tasks = [Task.from_dict(t) for t in data.get("tasks", [])]
        self.archived_tasks = [Task.from_dict(t) for t in data.get("archived", [])]
        self.task_id = data.get("task_id", 1)
        self.sound_on = data.get("sound_on", True)
        self._theme_name = data.get("theme", DEFAULT_THEME)
        self.notes = data.get("notes", {})

    # ---------- EXPORT REPORT ----------
    def export_readable_report(self):
        all_completed = self.archived_tasks + [t for t in self.tasks if t.completed]
        if not all_completed:
            return

        grouped = {}
        for t in all_completed:
            if not t.completed_at:
                continue
            day = t.completed_at.strftime("%Y-%m-%d")
            grouped.setdefault(day, []).append(t)

        lines = []
        for day in sorted(grouped.keys()):
            lines.append(f"=== {day} ===")
            for t in grouped[day]:
                folder = " / ".join(t.path) if t.path else "General"
                lines.append(f"✓ {t.text}  [{folder}]")
            lines.append("")

        with open(REPORT_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    # ---------- COLOR ----------
    def get_color(self, path):
        if not path:
            return "#bfe9ff"

        # Stable vivid neon hue per top-level project: hash the name onto the
        # full color wheel, with high saturation/lightness so every project
        # gets its own distinct color while staying in the cyberpunk-neon family.
        h = int(hashlib.md5(path[0].encode()).hexdigest(), 16)
        hue = (h % 360) / 360.0
        r, g, b = colorsys.hls_to_rgb(hue, 0.62, 0.95)
        return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"

    # ---------- TAGS / FILTER ----------
    def all_tags(self):
        tags = set()
        for t in self.tasks + self.archived_tasks:
            for tok in t.text.split():
                if tok.startswith("#") and len(tok) > 1:
                    tags.add(tok)
        return tags

    def folder_tree(self):
        # Maps a parent path tuple -> set of its child folder names, e.g.
        # () -> {"Work", "Personal"};  ("Work",) -> {"Backend"}.
        children = {}
        for t in self.tasks + self.archived_tasks:
            segs = [s for s in t.path if s]
            for i, seg in enumerate(segs):
                children.setdefault(tuple(segs[:i]), set()).add(seg)
        return children

    # ---------- NOTES ----------
    def _note_key(self, segs):
        # canonical "Folder/Sub" key from a list of path segments
        return "/".join(s.title() for s in segs if s)

    def _normalize_note_path(self, raw):
        # accept "/work/backend", "Work/Backend", etc. -> "Work/Backend"
        parts = [s.strip() for s in raw.replace("\\", "/").split("/") if s.strip()]
        return "/".join(s.title() for s in parts)

    def visible_tasks(self):
        tasks = self.tasks
        if self.filter_tag:
            tag = f"#{self.filter_tag}".lower()
            tasks = [t for t in tasks if tag in t.text.lower()]
        if self.filter_due:
            tasks = [t for t in tasks if self.match_due_filter(t)]
        return tasks

    def normalize_due_filter(self, spec):
        # Returns a keyword string, a date, or None if unrecognized.
        spec = spec.strip().lower().lstrip("@")
        if spec in ("today", "tod"):
            return "today"
        if spec in ("tomorrow", "tmr", "tom"):
            return "tomorrow"
        if spec in ("week", "7d"):
            return "week"
        if spec == "overdue":
            return "overdue"
        return self.parse_due(spec)

    def match_due_filter(self, t):
        f = self.filter_due
        today = datetime.now().date()
        if f == "overdue":
            return t.due is not None and t.due < today
        if f == "today":
            return t.due == today
        if f == "tomorrow":
            return t.due == today + timedelta(days=1)
        if f == "week":
            return t.due is not None and today <= t.due <= today + timedelta(days=7)
        if isinstance(f, date):
            return t.due == f
        return True

    def filter_label(self):
        bits = []
        if self.filter_tag:
            bits.append(f"#{self.filter_tag}")
        if self.filter_due:
            due = self.filter_due if isinstance(self.filter_due, str) else self.filter_due.isoformat()
            bits.append(f"due {due}")
        return " · ".join(bits)

    # ---------- GLOBAL ORDER ----------
    def get_open_tasks_ordered(self):
        # Due first (overdue/soonest on top), undated tasks last grouped by project.
        open_tasks = [t for t in self.visible_tasks() if not t.completed]
        open_tasks.sort(key=lambda t: (t.due or date.max, t.path, t.text))
        return open_tasks

    # ---------- GRAPH ----------
    def build_graph(self):
        if self.show_completed:
            # show the completed log in the big left pane so it's prominent
            return self.build_completed_view()

        p = self._palette()

        tree = {}
        for t in self.visible_tasks():
            node = tree
            for seg in t.path:
                if seg.strip():
                    node = node.setdefault(seg.title(), {})
            node.setdefault("_tasks", []).append(t)

        open_tasks = self.get_open_tasks_ordered()
        index_map = {id(t): i + 1 for i, t in enumerate(open_tasks)}

        lines = []
        if self.filter_tag or self.filter_due:
            lines.append(f"[{p.warning}]Filter: {self.filter_label()} (/filter to clear)[/]")

        def walk(node, prefix="", path=None):
            path = path or []
            keys = [k for k in node if k != "_tasks"]

            for i, k in enumerate(keys):
                connector = "└── " if i == len(keys) - 1 else "├── "
                color = self.get_color(path + [k])
                note_mark = " 📝" if self._note_key(path + [k]) in self.notes else ""
                lines.append(f"{prefix}{connector}[{color}]{k}[/]{note_mark}")
                walk(node[k], prefix + ("    " if i == len(keys) - 1 else "│   "), path + [k])

            if "_tasks" in node:
                # show tasks in the same due-first order as their numbers
                node_tasks = sorted(
                    node["_tasks"],
                    key=lambda t: (index_map.get(id(t), 10**9), t.text)
                )
                for i, t in enumerate(node_tasks):
                    connector = "└── " if i == len(node_tasks) - 1 else "├── "
                    is_current = t.id == self.current_task_id

                    display_id = "✓" if t.completed else index_map[id(t)]

                    style = self.get_color(t.path)
                    if is_current:
                        style = f"bold {p.accent}"
                    elif self.current_task_id and not t.completed:
                        style = "dim"

                    prefix_mark = "▶ " if is_current else ""
                    lines.append(
                        f"{prefix}{connector}[{style}]"
                        f"{prefix_mark}({display_id}) "
                        f"{'✓ ' if t.completed else ''}{self._display_text(t)}[/]"
                        + self.due_str(t)
                    )

        walk(tree)
        return "\n".join(lines)

    # ---------- TASK VIEW ----------
    def build_task_view(self):
        if self.show_completed:
            p = self._palette()
            return (
                f"[{p.warning}]✓ COMPLETED LOG[/]  [{self._muted()}](shown left ←)[/]\n\n"
                f"[{p.secondary}]{self.get_stats()}[/]\n\n"
                f"[{self._muted()}]/back to return[/]"
            )

        open_tasks = self.get_open_tasks_ordered()
        p = self._palette()

        lines = []
        if self.filter_tag or self.filter_due:
            lines.append(f"[{p.warning}]Filter: {self.filter_label()}[/]")

        for i, t in enumerate(open_tasks, start=1):
            is_current = t.id == self.current_task_id
            base_color = self.get_color(t.path)

            if is_current:
                style = f"bold {base_color}"
                prefix = "▶ "
            else:
                style = f"{base_color}"
                prefix = ""

            path = " / ".join(t.path) if t.path else "General"
            lines.append(
                f"[{style}]{prefix}({i}) {self._display_text(t)}[/] "
                f"[{base_color}][{path}][/]"
                + self.due_str(t)
            )

        if not lines:
            return f"[{self._muted()}]// no active tasks — awaiting input[/]"
        return "\n".join(lines)

    # ---------- COMPLETED ----------
    def build_completed_view(self):
        all_completed = self.archived_tasks + [t for t in self.tasks if t.completed]

        grouped = {}
        for t in all_completed:
            if t.completed_at:
                day = t.completed_at.strftime("%Y-%m-%d")
                grouped.setdefault(day, []).append(t)

        lines = []
        warn = self._palette().warning
        for day in sorted(grouped.keys(), reverse=True):
            lines.append(f"[{warn}]{day}[/]")
            for t in grouped[day]:
                lines.append(f"  ✓ {t.text}")
            lines.append("")

        return "\n".join(lines) if lines else (
            f"[{self._muted()}]// no completed tasks yet — finish one with /done <n>[/]"
        )

    # ---------- CLEANUP ----------
    def cleanup_completed_tasks(self):
        now = datetime.now()
        keep = []

        for t in self.tasks:
            if t.completed and t.completed_at:
                if now - t.completed_at > ARCHIVE_AFTER:
                    self.archived_tasks.append(t)
                else:
                    keep.append(t)
            else:
                keep.append(t)

        self.tasks = keep
        self.save_data()
        self.export_readable_report()
        self.refresh_all()

    # ---------- INPUT PARSING ----------
    def parse_due(self, s):
        # Accepts: today, tomorrow/tmr, <N>d, weekday (mon..sun / full), YYYY-MM-DD, MM-DD.
        s = s.strip().lower().lstrip("@")
        if not s:
            return None

        today = datetime.now().date()
        if s in ("today", "tod"):
            return today
        if s in ("tomorrow", "tmr", "tom"):
            return today + timedelta(days=1)

        weekdays = {
            "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
            "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6,
        }
        if s in weekdays:
            ahead = (weekdays[s] - today.weekday()) % 7 or 7
            return today + timedelta(days=ahead)

        if s.endswith("d") and s[:-1].isdigit():
            return today + timedelta(days=int(s[:-1]))

        try:
            return date.fromisoformat(s)
        except ValueError:
            pass

        parts = s.replace("/", "-").split("-")
        if len(parts) == 2 and all(p.isdigit() for p in parts):
            try:
                cand = date(today.year, int(parts[0]), int(parts[1]))
            except ValueError:
                return None
            if cand < today:
                cand = date(today.year + 1, int(parts[0]), int(parts[1]))
            return cand

        return None

    def parse_task_input(self, raw):
        # Pull out the due date — either "@<date>" anywhere, or the words
        # "due <date>" (e.g. "due today") — then parse the rest into text + path.
        due = None
        kept = []
        toks = raw.strip().split()
        i = 0
        while i < len(toks):
            tok = toks[i]
            if due is None and tok.startswith("@") and len(tok) > 1:
                parsed = self.parse_due(tok)
                if parsed is not None:
                    due = parsed
                    i += 1
                    continue
            if due is None and tok.lower() == "due" and i + 1 < len(toks):
                parsed = self.parse_due(toks[i + 1])
                if parsed is not None:
                    due = parsed
                    i += 2
                    continue
            kept.append(tok)
            i += 1

        text, path = " ".join(kept), []
        # Leading path (how /edit lays tasks out): "Folder/Sub  rest of text".
        if len(kept) >= 2 and "/" in kept[0]:
            path = [s.strip().title() for s in kept[0].split("/") if s.strip()]
            text = " ".join(kept[1:])
        # Trailing path (normal add): "task text  Folder/Sub" or "task Folder/".
        elif len(kept) >= 2 and "/" in kept[-1]:
            path = [s.strip().title() for s in kept[-1].split("/") if s.strip()]
            text = " ".join(kept[:-1])

        return text, path, due

    def task_to_input(self, t):
        # Path first, then due, so the editable task text sits at the end under
        # the cursor — no hunting for an edit spot in the middle of the line.
        parts = []
        if t.path:
            # keep a "/" even for a single folder so it round-trips as a path
            path_str = "/".join(t.path)
            if len(t.path) == 1:
                path_str += "/"
            parts.append(path_str)
        if t.due:
            parts.append("@" + t.due.isoformat())
        parts.append(t.text)
        return " ".join(parts)

    def due_str(self, t):
        # Small colored badge shown after a task; "" when no due date.
        if not t.due:
            return ""
        p = self._palette()
        delta = (t.due - datetime.now().date()).days
        if delta < 0:
            color, label = f"bold {p.error}", f"overdue {-delta}d"
        elif delta == 0:
            color, label = f"bold {p.warning}", "today"
        elif delta == 1:
            color, label = p.accent, "tomorrow"
        elif delta <= 7:
            color, label = p.secondary, t.due.strftime("%a")
        else:
            color, label = "#6b7a80", t.due.isoformat()
        return f" [{color}]⏳{label}[/]"

    # ---------- HINT LINE ----------
    def hint_for(self, value):
        # Faded line above the input; shows due-date options while entering one.
        low = value.lower()
        last = value.split(" ")[-1] if value else ""
        dates = "today · tomorrow · mon–sun · 3d · 2026-06-15"

        if low.startswith("/filter due"):
            return "[dim]filter by due: today · tomorrow · week · overdue · 2026-06-15[/]"
        if re.match(r"/edit\s+\d+\s", low) is not None:
            return f"[dim]set due date:  due tomorrow · due fri · due 2026-06-15   (dates: {dates})[/]"
        if low.startswith("/due ") or last.startswith("@"):
            return f"[dim]due options: {dates}[/]"
        if not value:
            return "[dim]task text · project as Work/Sub · due as @tomorrow · /help for commands[/]"
        return ""

    def on_input_changed(self, event: Input.Changed):
        self.hint_bar.update(self.hint_for(event.value))

    # ---------- INPUT ----------
    def on_input_submitted(self, event: Input.Submitted):
        raw = event.value.strip()
        event.input.value = ""

        if raw.startswith("/"):
            self.editing_task_id = None
            self.handle_command(raw)
        elif self.editing_task_id is not None:
            self.apply_edit(raw)
        elif raw:
            self.add_task(raw)

        self.refresh_all()
        self.save_data()

    # ---------- ADD / EDIT TASK ----------
    def add_task(self, raw):
        text, path, due = self.parse_task_input(raw)
        if not text:
            return
        task = Task(self.task_id, text, path, due)
        self.tasks.append(task)
        self.task_id += 1
        self._start_reveal(task)        # decrypt-style reveal of the new text

    def apply_edit(self, raw):
        task = next((t for t in self.tasks if t.id == self.editing_task_id), None)
        self.editing_task_id = None
        if task is None:
            return

        text, path, due = self.parse_task_input(raw)
        if not text:
            self.notify("Edit cancelled (empty text)")
            return

        task.text = text
        task.path = path
        task.due = due
        self.notify("Task updated")

    # ---------- COMMANDS ----------
    def handle_command(self, cmd):
        parts = cmd.split()

        if parts[0] == "/current":
            try:
                idx = int(parts[1])
                task = self.get_open_tasks_ordered()[idx - 1]
                self.current_task_id = task.id

                if len(parts) > 2:
                    mins = int(parts[2])
                    self.timer_end = datetime.now() + timedelta(minutes=mins)
                    self.timer_total = mins * 60
                    self._play("start")
            except (ValueError, IndexError):
                self.notify("Usage: /current <n> [minutes]")

        elif parts[0] == "/done" and len(parts) > 1:
            try:
                idx = int(parts[1])
                task = self.get_open_tasks_ordered()[idx - 1]
                task.completed = True
                task.completed_at = datetime.now()
                self._play("complete")

                if task.id == self.current_task_id:
                    self.current_task_id = None
                    self.timer_end = None
                    self.timer_total = None
            except (ValueError, IndexError):
                self.notify("Invalid number")

        elif parts[0] == "/edit" and len(parts) > 1:
            try:
                idx = int(parts[1])
                task = self.get_open_tasks_ordered()[idx - 1]
            except (ValueError, IndexError):
                self.notify("Usage: /edit <n>   or   /edit <n> @<due>")
            else:
                if len(parts) > 2:
                    # quick due-date edit — change only the date, leave text alone.
                    # accepts "/edit n due tomorrow" or "/edit n @tomorrow".
                    date_parts = parts[2:]
                    if date_parts[0].lower() == "due":
                        date_parts = date_parts[1:]
                    d = self.parse_due(" ".join(date_parts)) if date_parts else None
                    if d is None:
                        self.notify("Bad date. Try /edit n due tomorrow, due fri, due 2026-06-15")
                    else:
                        task.due = d
                        self.notify(f"Due {d.isoformat()}: {task.text}")
                else:
                    self.editing_task_id = task.id
                    self.input_box.value = self.task_to_input(task)
                    self.input_box.cursor_position = len(self.input_box.value)
                    self.input_box.focus()
                    self.notify(f"Editing ({idx}) — change the text and press Enter")

        elif parts[0] == "/delete" and len(parts) > 1:
            try:
                idx = int(parts[1])
                task = self.get_open_tasks_ordered()[idx - 1]
                self.tasks.remove(task)
                if task.id == self.current_task_id:
                    self.current_task_id = None
                    self.timer_end = None
                    self.timer_total = None
                if task.id == self.editing_task_id:
                    self.editing_task_id = None
                self.notify(f"Deleted: {task.text}")
            except (ValueError, IndexError):
                self.notify("Usage: /delete <n>")

        elif parts[0] == "/due" and len(parts) > 1:
            try:
                idx = int(parts[1])
                task = self.get_open_tasks_ordered()[idx - 1]
            except (ValueError, IndexError):
                self.notify("Usage: /due <n> <date>  (no date = clear)")
            else:
                if len(parts) > 2:
                    d = self.parse_due(parts[2])
                    if d is None:
                        self.notify("Bad date. Try /due n 2026-06-15, @fri, @3d")
                    else:
                        task.due = d
                        self.notify(f"Due {d.isoformat()}: {task.text}")
                else:
                    task.due = None
                    self.notify("Due date cleared")

        elif parts[0] == "/filter":
            if len(parts) >= 2 and parts[1].lower() == "due":
                if len(parts) >= 3:
                    self.filter_due = self.normalize_due_filter(parts[2])
                    if self.filter_due is None:
                        self.notify("Due filter: today | tomorrow | week | overdue | YYYY-MM-DD")
                    else:
                        self.filter_tag = None
                        self.notify(f"Filtering by due: {self.filter_label()}")
                else:
                    self.filter_due = None
                    self.notify("Due filter cleared")
            elif len(parts) > 1:
                self.filter_tag = parts[1].lstrip("#")
                self.filter_due = None
                self.notify(f"Filtering by #{self.filter_tag}")
            else:
                self.filter_tag = None
                self.filter_due = None
                self.notify("Filter cleared")

        elif cmd == "/completed":
            self.show_completed = True

        elif cmd == "/back":
            self.show_completed = False

        elif cmd == "/clear all":
            self.tasks.clear()
            self.archived_tasks.clear()
            self.current_task_id = None
            self.timer_end = None
            self.timer_total = None
            self.editing_task_id = None
            self.filter_tag = None
            self.filter_due = None

        elif cmd == "/export":
            self.export_readable_report()
            self.notify("Report exported")

        elif cmd == "/sound":
            self.sound_on = not self.sound_on
            self.notify(f"Sound {'ON' if self.sound_on else 'OFF'}")
            self._play("toggle")

        elif parts[0] in ("/notes", "/note"):
            if len(parts) > 1:
                path = self._normalize_note_path(" ".join(parts[1:]))
                if path:
                    self.push_screen(NotesScreen(path))
                else:
                    self.notify("Usage: /notes <folder/path>   (or /notes to browse)")
            else:
                self.push_screen(NotesBrowserScreen())

        elif parts[0] == "/theme":
            if len(parts) > 1:
                name = parts[1].lower()
                if name in self.available_themes:
                    self.theme = name
                    self._theme_name = name
                    self.notify(f"Theme: {name}")
                else:
                    self.notify(f"Unknown theme '{name}'. Type /theme to list them.")
            else:
                names = "  ".join(sorted(self.available_themes))
                self.notify(f"Current: {self.theme}\nThemes: {names}\nUse /theme <name>", timeout=15)

        elif cmd == "/help":
            self.notify(
                "ADD TASK: text, optional 'project/sub', optional due date\n"
                "          e.g.  pay rent #bills Money/Home due today\n"
                "  due forms: @today / due today · @fri / due fri · @3d · 2026-06-01\n"
                "\n"
                "/current <n> [minutes]  set active task + pomodoro timer\n"
                "/done <n>               complete task n\n"
                "/edit <n>               load task n into the box to edit\n"
                "/edit <n> due <date>    quickly set just the due date (e.g. due tomorrow)\n"
                "/due <n> [date]         set/clear due date (blank = clear)\n"
                "/delete <n>             delete task n\n"
                "/filter <tag>           show only tasks with #tag (blank = clear)\n"
                "/filter due <when>      by due date: today|tomorrow|week|overdue|YYYY-MM-DD\n"
                "/completed              view completed tasks\n"
                "/back                   leave the completed view\n"
                "/clear all              delete all tasks and archive\n"
                "/export                 write completed_report.txt\n"
                "/sound                  toggle completion/timer sounds\n"
                "/notes [folder/path]    edit a folder's note (blank = search notes)\n"
                "/theme [name]           switch color theme (blank = list; e.g. dracula)\n"
                "/help                   show this help\n",
                timeout=18
            )

    # ---------- REFRESH ----------
    def refresh_all(self):
        self.graph_view.update(self.build_graph())
        self.task_view.update(self.build_task_view())
        self.status_bar.update(self.render_big_timer())


# ---------- RUN ----------
if __name__ == "__main__":
    TodoApp().run()
