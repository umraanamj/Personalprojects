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
from collections import Counter

# glyphs used for the "decrypt" scramble effect and title glitch
SCRAMBLE_GLYPHS = "!@#$%&*+=/\\<>?▓▒░01x"

# strips Rich markup tags (e.g. "[bold #fff]") so we can measure the *visible*
# width of a styled string — needed to align the project tiles in the grid.
MARKUP_RE = re.compile(r"\[/?[^\[\]]*\]")

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
# Watch Dogs ctOS terminal palette — cyan + amber on near-black.
CTOS_THEME = Theme(
    name="ctos",
    primary="#36e0e0",     # ctOS cyan
    secondary="#5a7a85",   # muted steel
    accent="#ff7a1a",      # ctOS amber
    foreground="#dff6f6",
    background="#0a0e10",
    surface="#10171a",
    panel="#10171a",
    success="#36e0e0",
    warning="#ff7a1a",
    error="#ff3b3b",
    dark=True,
)
DEFAULT_THEME = "cp2077"

# app build version — shown on the boot/title screen and the header subtitle
APP_VERSION = "1.5"

# ---------- TIMESHEET CATEGORIES ----------
# (code, label, shortcut tokens). Type the number or any keyword.
# "personal" is special: tasks tagged personal are never billed to the
# timesheet and never appear in the schedule (see _log_time / render_schedule).
PERSONAL_CAT = "personal"
CATEGORIES = [
    ("admin", "Admin", ("admin", "1")),
    ("dev",   "Develop Self", ("dev", "self", "2")),
    ("kt",    "Onboarding / Knowledge Transfer", ("kt", "onboard", "onboarding", "3")),
    ("prod",  "Production Support", ("prod", "support", "ps", "4")),
    ("run",   "Run the Business", ("run", "rtb", "5")),
    ("plan",  "Plan — strategy / research / discovery", ("plan", "6")),
    ("build", "Build — dev / config / defects / refinement / sprint", ("build", "7")),
    ("personal", "Personal — not time-tracked", ("personal", "pers", "8")),
]
CATEGORY_LABEL = {code: label for code, label, _ in CATEGORIES}
CATEGORY_SHORT = {code: label.split("—")[0].strip() for code, label, _ in CATEGORIES}
CATEGORY_ALIASES = {}
for _code, _label, _aliases in CATEGORIES:
    CATEGORY_ALIASES[_code] = _code
    for _a in _aliases:
        CATEGORY_ALIASES[_a] = _code


def resolve_category(token):
    return CATEGORY_ALIASES.get(token.strip().lower())

# ---------- HELP CONTENT ----------
HELP_SECTIONS = [
    ("tasks", "TASKS", [
        "Add a task — just type it, with optional parts in any order:",
        "  Project/Sub   folder path (or 'Folder/' for a single level)",
        "  #tag          a label (any number of them)",
        "  +category     timesheet category (admin/dev/kt/prod/run/plan/build)",
        "  @today        a due date (also: due today, @fri, due fri, @3d, 2026-06-15)",
        "  e.g.  pay rent #bills Money/Home +admin due fri",
        "",
        "/done <n>             complete task n",
        "/done <n> <time>      complete task n and log time (e.g. 90, 1h30m)",
        "/edit <n>             load task n into the input to edit",
        "/edit <n> due <date>  quickly change only the due date",
        "/due <n> [date]       set or clear the due date (blank clears)",
        "/cat <n> <category>   assign a category (blank clears)",
        "/auditcat             review every uncategorized task; press 1-8 to assign",
        "/delete <n>           delete task n",
        "/undo                 undo the last change (repeatable)",
    ]),
    ("time", "TIME TRACKING", [
        "/current <n>              stopwatch on task n (uses the task's category)",
        "/current <n> <min>        pomodoro countdown on task n",
        "/current <n> [min] <cat>  ...also tag the time to a category",
        "/track <cat>              live stopwatch for a category (no task)",
        "/track <cat> <min>        log <min> to a category now (e.g. /track 1 60)",
        "/stop                     stop the active timer and log it",
        "/categories               list categories and their shortcuts",
        "",
        "All logged time rounds UP to the nearest 30 minutes.",
        "Categories: admin·1  dev·2  kt·3  prod·4  run·5  plan·6  build·7  personal·8",
        "",
        "Closing a task with no time given logs 30m to its category.",
        "Tasks marked +personal are never time-tracked and never appear in",
        "the schedule (and closing one logs nothing).",
    ]),
    ("schedule", "SCHEDULE / TIMESHEET", [
        "/schedule    open the timesheet, then:",
        "  ← / →      previous / next period",
        "  d / w      day or week view",
        "  t          toggle category vs task breakdown",
        "  Esc        close",
    ]),
    ("stats", "STATS", [
        "/stats   productivity dashboard — completed counts, most-productive",
        "         day/week/month/year, streaks, top projects, open-task split",
    ]),
    ("notes", "NOTES", [
        "/notes <folder/path>   open or edit that folder's note",
        "/notes                 search all your notes",
    ]),
    ("filter", "VIEW & FILTER", [
        "/folderview          full-screen view of every parent folder + tasks",
        "/filter <tag>        show only tasks with #tag (blank clears)",
        "/filter due <when>   today | tomorrow | week | overdue | YYYY-MM-DD",
        "/completed           view completed tasks (grouped by day)",
        "/clearcomplete       archive all completed tasks (clears the view)",
        "/back                leave the completed view (or just press Esc)",
        "",
        "Esc always goes back: it closes any page, leaves the completed view,",
        "or clears an active filter.",
    ]),
    ("app", "APP", [
        "/theme [name]   switch color theme (blank lists them)",
        "/sound          toggle completion / timer sounds",
        "/export         write completed_report.txt",
        "/clear all      delete all tasks + archive (keeps your timesheet)",
        "/help [topic]   this help — e.g. /help schedule, /help time",
    ]),
]
# map many tokens (and command names) onto a section key
HELP_ALIASES = {
    "tasks": "tasks", "task": "tasks", "add": "tasks", "done": "tasks",
    "edit": "tasks", "due": "tasks", "delete": "tasks", "cat": "tasks",
    "undo": "tasks",
    "auditcat": "tasks", "audit": "tasks",
    "time": "time", "track": "time", "current": "time", "stop": "time",
    "timer": "time", "pomodoro": "time", "categories": "time", "tracking": "time",
    "schedule": "schedule", "timesheet": "schedule",
    "stats": "stats", "stat": "stats",
    "notes": "notes", "note": "notes",
    "filter": "filter", "view": "filter", "completed": "filter", "back": "filter",
    "clearcomplete": "filter", "clearcompleted": "filter",
    "folderview": "filter", "folders": "filter",
    "app": "app", "theme": "app", "sound": "app", "export": "app", "clear": "app",
    "help": "app",
}

# one-line descriptions shown in the hint line while typing a /command
COMMAND_DESCRIPTIONS = {
    "/help": "scrollable help (try /help <topic>)",
    "/current": "track time on a task — stopwatch, or pomodoro with <min>",
    "/done": "complete a task (add a time to log it, e.g. /done 2 90)",
    "/edit": "edit a task (or /edit <n> due <date>)",
    "/due": "set or clear a task's due date",
    "/delete": "delete a task",
    "/undo": "undo the last change (add/done/delete/edit/due/cat/track…)",
    "/filter": "show only #tag or by due date",
    "/completed": "view completed tasks, grouped by day",
    "/clearcomplete": "archive all completed tasks (clears them from the view)",
    "/back": "leave the completed view",
    "/clear all": "delete all tasks + archive (keeps timesheet)",
    "/export": "write completed_report.txt",
    "/sound": "toggle completion / timer sounds",
    "/theme": "switch color theme (blank = list)",
    "/notes": "edit a folder's note (blank = search notes)",
    "/stats": "productivity dashboard",
    "/track": "log or track time to a category",
    "/stop": "stop the active timer and log it",
    "/schedule": "timesheet by day / week",
    "/categories": "list categories + shortcuts",
    "/cat": "assign a category to a task",
    "/auditcat": "review uncategorized tasks; press 1–8 to assign",
    "/folderview": "full-screen view of every parent folder and its tasks",
}

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
    def __init__(self, id, text, path=None, due=None, created_at=None, category=None):
        self.id = id
        self.text = text
        self.path = path or []
        self.due = due
        self.completed = False
        self.completed_at = None
        self.created_at = created_at  # when the task was added (for time-to-complete)
        self.category = category      # timesheet category code, or None

    def to_dict(self):
        return {
            "id": self.id,
            "text": self.text,
            "path": self.path,
            "due": self.due.isoformat() if self.due else None,
            "completed": self.completed,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "category": self.category,
        }

    @staticmethod
    def from_dict(data):
        t = Task(data["id"], data["text"], data.get("path"))
        t.completed = data.get("completed", False)
        if data.get("due"):
            t.due = date.fromisoformat(data["due"])
        if data.get("completed_at"):
            t.completed_at = datetime.fromisoformat(data["completed_at"])
        if data.get("created_at"):
            t.created_at = datetime.fromisoformat(data["created_at"])
        t.category = data.get("category")
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

        # +category completion (e.g. +bu -> +build)
        if last.startswith("+") and not value.startswith("/"):
            word = last[1:].lower()
            prefix = value[:cut + 1]
            for code, _label, _aliases in CATEGORIES:
                if code.startswith(word) and code != word:
                    return prefix + "+" + code

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
    """An Input where Tab accepts the autocomplete suggestion (just like →),
    and Esc steps back out of the current view (matching the other screens)."""

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
        if event.key == "escape" and self.app.go_back():
            event.stop()
            event.prevent_default()
            return
        await super()._on_key(event)


# ---------- BOOT SEQUENCE ----------
class BootScreen(Screen):
    """A throwaway startup log that types itself out, then drops into the app."""

    LINES = [
        "> ctOS TERMINAL  v3.1",
        f"> TODO//2077  ·  BUILD v{APP_VERSION}",
        "> ESTABLISHING UPLINK ......... OK",
        "> AUTHENTICATING .............. OK",
        "> SYNCING NODES [{n}] ......... OK",
        "> ACCESS GRANTED",
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


# ---------- PURGE SPLASH ----------
class PurgeScreen(Screen):
    """Brief on-theme flash shown while /clear all wipes everything."""

    def compose(self) -> ComposeResult:
        yield Static("⚠  PURGING DATABASE …\n\n   flushing all records", id="purge")

    def on_mount(self):
        self.set_timer(0.7, self._finish)

    def _finish(self):
        self.app._do_clear_all()
        self.app.pop_screen()


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
        self.app.notify(f"▸ NOTE SAVED · {self.path}")

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


# ---------- SCHEDULE / TIMESHEET ----------
class ScheduleScreen(Screen):
    """Timesheet view: day/week breakdown by category or by task."""

    BINDINGS = [
        ("escape", "close", "close"),
        ("left", "prev", "prev"),
        ("right", "next", "next"),
        ("d", "day", "day"),
        ("w", "week", "week"),
        ("t", "toggle", "toggle view"),
    ]

    def __init__(self):
        super().__init__()
        self.mode = "week"
        self.view = "category"
        self.anchor = datetime.now().date()

    def compose(self) -> ComposeResult:
        yield Static("  📅 SCHEDULE", id="sched-title")
        with VerticalScroll(id="sched-scroll"):
            yield Static(self.app.render_schedule(self.mode, self.anchor, self.view),
                         id="sched-body")
        yield Static("  ←/→ move · d day · w week · t category/task · Esc back",
                     id="sched-help")

    def _refresh_body(self):
        self.query_one("#sched-body").update(
            self.app.render_schedule(self.mode, self.anchor, self.view))

    def action_prev(self):
        self.anchor -= timedelta(days=7 if self.mode == "week" else 1)
        self._refresh_body()

    def action_next(self):
        self.anchor += timedelta(days=7 if self.mode == "week" else 1)
        self._refresh_body()

    def action_day(self):
        self.mode = "day"
        self._refresh_body()

    def action_week(self):
        self.mode = "week"
        self._refresh_body()

    def action_toggle(self):
        self.view = "task" if self.view == "category" else "category"
        self._refresh_body()

    def action_close(self):
        self.app.pop_screen()


# ---------- CATEGORY AUDIT ----------
class AuditCategoriesScreen(Screen):
    """Walk through uncategorized tasks; press 1-7 to assign a category."""

    BINDINGS = [
        ("1", "assign(1)", "admin"), ("2", "assign(2)", "dev"),
        ("3", "assign(3)", "kt"), ("4", "assign(4)", "prod"),
        ("5", "assign(5)", "run"), ("6", "assign(6)", "plan"),
        ("7", "assign(7)", "build"), ("8", "assign(8)", "personal"),
        ("s", "skip", "skip"),
        ("escape", "close", "quit"),
    ]

    def __init__(self, task_ids):
        super().__init__()
        self.queue = task_ids
        self.idx = 0
        self.assigned = 0

    def compose(self) -> ComposeResult:
        yield Static("  🏷  ASSIGN CATEGORIES", id="audit-title")
        with VerticalScroll(id="audit-scroll"):
            yield Static(self._body_text(), id="audit-body")
        yield Static("  press 1–8 to assign · s skip · Esc back", id="audit-help")

    def _current(self):
        # advance past anything already categorized / completed / deleted
        while self.idx < len(self.queue):
            t = next((x for x in self.app.tasks if x.id == self.queue[self.idx]), None)
            if t and not t.category and not t.completed:
                return t
            self.idx += 1
        return None

    def _body_text(self):
        app = self.app
        p = app._palette()
        t = self._current()
        if t is None:
            return f"[{app._muted()}]✓ all reviewed — {self.assigned} assigned. Esc to close.[/]"
        path = " / ".join(t.path) if t.path else "General"
        due = self.app.due_str(t).strip()
        lines = [
            f"[{p.secondary}]task {self.idx + 1} of {len(self.queue)}[/]",
            "",
            f"  [bold {p.foreground}]{t.text}[/]  [{p.secondary}][{path}][/]"
            + (f"  {due}" if due else ""),
            "",
            f"  [{p.secondary}]assign a category:[/]",
        ]
        for i, (code, label, _aliases) in enumerate(CATEGORIES, start=1):
            lines.append(f"    [{app.get_color([code])}]{i}[/]  {label}")
        return "\n".join(lines)

    def _refresh(self):
        self.query_one("#audit-body").update(self._body_text())

    def _after(self):
        if self._current() is None:
            self.action_close()
        else:
            self._refresh()

    def action_assign(self, n):
        t = self._current()
        if t:
            t.category = CATEGORIES[n - 1][0]
            self.assigned += 1
            self.app.save_data()
        self.idx += 1
        self._after()

    def action_skip(self):
        self.idx += 1
        self._after()

    def action_close(self):
        self.app.refresh_all()
        self.app.pop_screen()
        if self.assigned:
            self.app.notify(f"▸ {self.assigned} task(s) categorized")


# ---------- HELP ----------
class HelpScreen(Screen):
    """Scrollable help — all commands, or one topic via /help <topic>."""

    BINDINGS = [("escape", "close", "close")]

    def __init__(self, topic=None):
        super().__init__()
        self.topic = topic

    def compose(self) -> ComposeResult:
        title = "  ❓ HELP" + (f" · {self.topic}" if self.topic else "")
        yield Static(title, id="help-title")
        with VerticalScroll(id="help-scroll"):
            yield Static(self.app.render_help(self.topic), id="help-body")
        yield Static("  ↑/↓ or mouse-wheel to scroll · Esc back", id="help-help")

    def on_mount(self):
        self.query_one("#help-scroll").focus()  # so arrow keys scroll

    def action_close(self):
        self.app.pop_screen()


# ---------- STATS ----------
class StatsScreen(Screen):
    """Scrollable productivity dashboard."""

    BINDINGS = [("escape", "close", "close")]

    def compose(self) -> ComposeResult:
        yield Static("  📊 STATS", id="stats-title")
        with VerticalScroll(id="stats-scroll"):
            yield Static(self.app.render_stats(), id="stats-body")
        yield Static("  Esc back", id="stats-help")

    def action_close(self):
        self.app.pop_screen()


# ---------- FOLDER VIEW ----------
class FolderViewScreen(Screen):
    """Full-screen overview of every top-level project and its open tasks."""

    BINDINGS = [("escape", "close", "close")]

    def compose(self) -> ComposeResult:
        yield Static("  🗂  FOLDER VIEW", id="folder-title")
        with VerticalScroll(id="folder-scroll"):
            yield Static(self.app.render_folderview(), id="folder-body")
        yield Static("  ↑/↓ or mouse-wheel to scroll · Esc back", id="folder-help")

    def on_mount(self):
        self.query_one("#folder-scroll").focus()

    def action_close(self):
        self.app.pop_screen()


# ---------- APP ----------
class TodoApp(App):
    TITLE = "TODO//2077"
    SUB_TITLE = f"night city task grid · v{APP_VERSION}"

    COMMANDS = [
        "/help", "/current", "/done", "/edit", "/due", "/delete", "/undo",
        "/filter", "/completed", "/clearcomplete", "/back", "/clear all",
        "/export", "/sound",
        "/theme", "/notes", "/stats", "/track", "/stop", "/schedule",
        "/categories", "/cat", "/auditcat", "/folderview",
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

    /* purge confirmation splash for /clear all */
    PurgeScreen { align: center middle; background: $background; }
    #purge { width: auto; padding: 2 4; border: heavy $error; color: $error; text-style: bold; }

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

    /* stats dashboard */
    StatsScreen { background: $background; }
    #stats-title { height: 1; padding: 0 1; background: $primary; color: $background; text-style: bold; }
    #stats-help { height: 1; padding: 0 1; color: $secondary; }
    #stats-scroll { height: 1fr; border: round $accent; padding: 1 2; }
    #stats-body { height: auto; }

    /* folder view */
    FolderViewScreen { background: $background; }
    #folder-title { height: 1; padding: 0 1; background: $primary; color: $background; text-style: bold; }
    #folder-help { height: 1; padding: 0 1; color: $secondary; }
    #folder-scroll { height: 1fr; border: round $accent; padding: 1 2; }
    #folder-body { height: auto; }

    /* category audit */
    AuditCategoriesScreen { background: $background; }
    #audit-title { height: 1; padding: 0 1; background: $primary; color: $background; text-style: bold; }
    #audit-help { height: 1; padding: 0 1; color: $secondary; }
    #audit-scroll { height: 1fr; border: round $accent; padding: 1 2; }
    #audit-body { height: auto; }

    /* help */
    HelpScreen { background: $background; }
    #help-title { height: 1; padding: 0 1; background: $primary; color: $background; text-style: bold; }
    #help-help { height: 1; padding: 0 1; color: $secondary; }
    #help-scroll { height: 1fr; border: round $accent; padding: 1 2; }
    #help-body { height: auto; }

    /* schedule / timesheet */
    ScheduleScreen { background: $background; }
    #sched-title { height: 1; padding: 0 1; background: $primary; color: $background; text-style: bold; }
    #sched-help { height: 1; padding: 0 1; color: $secondary; }
    #sched-scroll { height: 1fr; border: round $accent; padding: 1 2; }
    #sched-body { height: auto; }
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
        self.timer_mode = None          # None | "countdown" | "countup"
        self.timer_start = None         # when the active timer began
        self.timer_category = None      # timesheet category code, or None
        self.timer_task_text = None     # cached task text for logging
        self.time_entries = []          # logged timesheet blocks (rounded to 30m)
        self.editing_task_id = None
        self.filter_tag = None
        self.filter_due = None
        self.notes = {}  # folder-path string -> free-form note text
        self._undo_stack = []   # snapshots of task data, for /undo
        # animation / telemetry state
        self.start_time = datetime.now()
        self.blink = False
        self._tick_count = 0
        self._title_base = self.TITLE
        self._reveal = None
        self._reveal_timer = None
        self._redact = None          # glitch flash on task completion
        self._redact_timer = None
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
        self.register_theme(CTOS_THEME)
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

    def _start_redact(self, task):
        # brief glitch flash when a task is completed (one-shot, then stops)
        self._redact = {"id": task.id, "frames": 8}
        if self._redact_timer:
            self._redact_timer.stop()
        self._redact_timer = self.set_interval(0.04, self._redact_tick)

    def _redact_tick(self):
        if not self._redact:
            return
        self._redact["frames"] -= 1
        if self._redact["frames"] <= 0:
            self._redact = None
            if self._redact_timer:
                self._redact_timer.stop()
                self._redact_timer = None
        self.refresh_all()

    def _display_text(self, t):
        # mid-completion: flash scrambled glyphs (redaction), then settle
        rd = self._redact
        if rd and rd["id"] == t.id:
            return "".join(random.choice(SCRAMBLE_GLYPHS) if c != " " else " "
                           for c in t.text)
        # mid-add: "decrypt" reveal — scramble the not-yet-typed tail
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
    def _round30(self, minutes):
        # round UP to the nearest 30 minutes (min 30)
        return max(30, math.ceil(minutes / 30) * 30)

    def _parse_minutes(self, text):
        """Parse a friendly duration into minutes, or None if unparseable.
        Accepts '90' / '90m' (minutes), '1h' / '1.5h' (hours), and
        combos like '1h30m' or '1h30'."""
        s = text.strip().lower().replace(" ", "")
        if not s:
            return None
        if re.fullmatch(r"\d+", s):                       # bare number = minutes
            return int(s)
        m = re.fullmatch(r"(\d+(?:\.\d+)?)h", s)          # decimal hours, e.g. 1.5h
        if m:
            return int(round(float(m.group(1)) * 60))
        m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m?)?", s)   # 1h30m / 1h30 / 45m
        if m and (m.group(1) or m.group(2)):
            return int(m.group(1) or 0) * 60 + int(m.group(2) or 0)
        return None

    def is_personal(self, task):
        return task is not None and task.category == PERSONAL_CAT

    def _log_time(self, minutes, category, task_text):
        # personal work is never billed to the timesheet / schedule
        if category == PERSONAL_CAT:
            return None
        rounded = self._round30(minutes)
        self.time_entries.append({
            "date": datetime.now().date().isoformat(),
            "category": category or "",
            "task": task_text or "",
            "minutes": rounded,
        })
        return rounded

    def _start_countdown(self, mins, category, task_id, task_text):
        self._stop_timer(log=True)
        now = datetime.now()
        self.timer_mode = "countdown"
        self.timer_start = now
        self.timer_end = now + timedelta(minutes=mins)
        self.timer_total = mins * 60
        self.timer_category = category
        self.timer_task_text = task_text
        self.current_task_id = task_id
        self._play("start")

    def _start_countup(self, category, task_id, task_text):
        self._stop_timer(log=True)
        self.timer_mode = "countup"
        self.timer_start = datetime.now()
        self.timer_end = None
        self.timer_total = None
        self.timer_category = category
        self.timer_task_text = task_text
        self.current_task_id = task_id
        self._play("start")

    def _stop_timer(self, log=True, announce=False):
        if not self.timer_mode:
            return None
        elapsed_min = (datetime.now() - self.timer_start).total_seconds() / 60
        cat, task_text = self.timer_category, self.timer_task_text
        rounded = self._log_time(elapsed_min, cat, task_text) if log else None
        self.timer_mode = None
        self.timer_start = None
        self.timer_end = None
        self.timer_total = None
        self.timer_category = None
        self.timer_task_text = None
        if announce and rounded:
            label = CATEGORY_LABEL.get(cat, cat) if cat else (task_text or "untracked")
            self.notify(f"▓ LOGGED {rounded}m · {label}")
        return rounded

    def update_timer(self):
        if not self.timer_mode:
            return
        if self.timer_mode == "countdown" and datetime.now() >= self.timer_end:
            cat, task_text = self.timer_category, self.timer_task_text
            mins = (self.timer_total or 0) / 60
            rounded = self._log_time(mins, cat, task_text)
            self.timer_mode = None
            self.timer_end = None
            self.timer_total = None
            self.timer_start = None
            self.timer_category = None
            self.timer_task_text = None
            self.current_task_id = None
            label = CATEGORY_LABEL.get(cat, cat) if cat else (task_text or "session")
            if rounded:
                self.notify(f"▓ OP COMPLETE · logged {rounded}m · {label}")
            else:
                self.notify(f"▓ OP COMPLETE · {label}")
            self._play("win")
            self.save_data()
        self.refresh_all()

    def format_timer(self):
        if self.timer_mode == "countdown":
            sec = max(0, int((self.timer_end - datetime.now()).total_seconds()))
        elif self.timer_mode == "countup":
            sec = max(0, int((datetime.now() - self.timer_start).total_seconds()))
        else:
            return "00:00"
        m, s = divmod(sec, 60)
        return f"{m:02}:{s:02}"

    def timer_progress(self):
        # Returns (remaining_sec, total_sec, fraction_elapsed) or None.
        if self.timer_mode != "countdown" or not self.timer_total:
            return None
        remaining = max(0.0, (self.timer_end - datetime.now()).total_seconds())
        total = self.timer_total
        frac = 1 - remaining / total if total else 1.0
        return remaining, total, max(0.0, min(1.0, frac))

    def render_big_timer(self):
        txt = self.format_timer()
        prog = self.timer_progress()
        p = self._palette()

        running = self.timer_mode is not None
        if prog:  # countdown / pomodoro
            remaining, total, frac = prog
            rem_frac = remaining / total if total else 0
            color = p.success if rem_frac > 0.5 else (p.warning if rem_frac > 0.2 else p.error)
        elif self.timer_mode == "countup":
            frac = 0.0
            color = p.accent
        else:
            frac = 0.0
            color = p.primary

        # blink the colon once per heartbeat while a timer is running
        if running and not self.blink:
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

        if running:
            mode = "POMODORO" if self.timer_mode == "countdown" else "TRACKING"
            who = self.timer_task_text or "session"
            cat = self.timer_category
            tag = f" · [{p.accent}]{CATEGORY_LABEL.get(cat, cat)}[/]" if cat else ""
            info = f"[{color}]{mode}[/] · {who}{tag}   [bold]{self.get_stats()}[/]"
        else:
            info = (f"[bold]{self.get_stats()}[/]   "
                    f"[dim]· /current <n> [min] [cat] · /track <cat> · /stop[/]")

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

    # ---------- DETAILED STATS ----------
    def detailed_stats(self):
        done = [t for t in (self.archived_tasks + [x for x in self.tasks if x.completed])
                if t.completed_at]
        now = datetime.now()
        today = now.date()
        week_start = today - timedelta(days=now.weekday())

        day_counts = Counter(t.completed_at.date() for t in done)

        def top_day(pred):
            cand = [(d, c) for d, c in day_counts.items() if pred(d)]
            return max(cand, key=lambda x: (x[1], x[0])) if cand else None

        # current streak: consecutive days with completions ending today (or
        # yesterday, so an unfinished today doesn't read as a broken streak)
        def run_back(start):
            n, d = 0, start
            while day_counts.get(d, 0) > 0:
                n += 1
                d -= timedelta(days=1)
            return n
        current_streak = run_back(today) or run_back(today - timedelta(days=1))

        # longest streak ever
        longest = 0
        for d in sorted(day_counts):
            longest = max(longest, run_back(d))

        # last 7 days of activity (oldest -> newest), for the sparkline
        last7 = [day_counts.get(today - timedelta(days=i), 0) for i in range(6, -1, -1)]

        # average time from creation to completion (only tasks that recorded it)
        durations = [(t.completed_at - t.created_at).total_seconds()
                     for t in done if t.created_at and t.completed_at >= t.created_at]
        avg_secs = sum(durations) / len(durations) if durations else None

        # completion rate: done vs. (done + still-open) currently tracked
        open_tasks = [t for t in self.tasks if not t.completed]
        denom = len(done) + len(open_tasks)
        rate = round(len(done) / denom * 100) if denom else 0

        # top #tags among completed tasks
        tag_counts = Counter(
            tok for t in done for tok in t.text.split()
            if tok.startswith("#") and len(tok) > 1
        ).most_common(5)

        return {
            "last7": last7,
            "avg_secs": avg_secs,
            "rate": rate,
            "tag_counts": tag_counts,
            "today": day_counts.get(today, 0),
            "week": sum(c for d, c in day_counts.items() if week_start <= d <= today),
            "month": sum(c for d, c in day_counts.items()
                         if d.year == now.year and d.month == now.month),
            "year": sum(c for d, c in day_counts.items() if d.year == now.year),
            "total": len(done),
            "week_top": top_day(lambda d: week_start <= d <= today),
            "month_top": top_day(lambda d: d.year == now.year and d.month == now.month),
            "year_top": top_day(lambda d: d.year == now.year),
            "best_day": top_day(lambda d: True),
            "top_month": (Counter(t.completed_at.month for t in done
                                  if t.completed_at.year == now.year).most_common(1) or [None])[0],
            "busiest_wd": (Counter(t.completed_at.weekday() for t in done).most_common(1)
                           or [None])[0],
            "current_streak": current_streak,
            "longest_streak": longest,
            "avg_month": (sum(c for d, c in day_counts.items()
                          if d.year == now.year and d.month == now.month) / today.day),
            "top_projects": Counter((t.path[0] if t.path else "General")
                                    for t in done).most_common(5),
            "open": len(open_tasks),
            "overdue": sum(1 for t in open_tasks if t.due and t.due < today),
            "due_today": sum(1 for t in open_tasks if t.due == today),
            "due_week": sum(1 for t in open_tasks
                            if t.due and today <= t.due <= today + timedelta(days=7)),
            "no_due": sum(1 for t in open_tasks if not t.due),
        }

    def render_stats(self):
        WD = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        MO = ["", "January", "February", "March", "April", "May", "June", "July",
              "August", "September", "October", "November", "December"]
        p = self._palette()
        s = self.detailed_stats()

        if s["total"] == 0:
            return f"[{self._muted()}]// no completed tasks yet — finish one with /done <n>[/]"

        def hdr(t):
            return f"[bold {p.primary}]{t}[/]"

        def daystr(pair):
            if not pair:
                return f"[{self._muted()}]—[/]"
            d, c = pair
            return f"[{p.accent}]{d.strftime('%a %b %d')}[/]  [{p.secondary}]{c} done[/]"

        lines = []
        lines.append(hdr("COMPLETED"))
        lines.append(f"  Today        {s['today']}")
        lines.append(f"  This week    {s['week']}")
        lines.append(f"  This month   {s['month']}")
        lines.append(f"  This year    {s['year']}")
        lines.append(f"  Total        {s['total']}")
        lines.append("")
        lines.append(hdr("MOST PRODUCTIVE DAY"))
        lines.append(f"  This week    {daystr(s['week_top'])}")
        lines.append(f"  This month   {daystr(s['month_top'])}")
        lines.append(f"  This year    {daystr(s['year_top'])}")
        lines.append(f"  Best ever    {daystr(s['best_day'])}")
        if s["top_month"]:
            lines.append(f"  Top month    [{p.accent}]{MO[s['top_month'][0]]}[/]  "
                         f"[{p.secondary}]{s['top_month'][1]} done[/]")
        if s["busiest_wd"] is not None:
            lines.append(f"  Busiest day  [{p.accent}]{WD[s['busiest_wd'][0]]}[/]")
        lines.append("")
        def fmt_dur(secs):
            if secs is None:
                return f"[{self._muted()}]—[/]"
            if secs >= 86400:
                return f"{secs / 86400:.1f} days"
            if secs >= 3600:
                return f"{secs / 3600:.1f} h"
            return f"{secs / 60:.0f} min"

        lines.append(hdr("MOMENTUM"))
        lines.append(f"  Current streak   [{p.accent}]{s['current_streak']} day(s)[/]")
        lines.append(f"  Longest streak   {s['longest_streak']} day(s)")
        lines.append(f"  Avg this month   {s['avg_month']:.1f} / day")
        lines.append(f"  Completion rate  {s['rate']}%  [{self._muted()}](done vs open)[/]")
        lines.append(f"  Avg to complete  {fmt_dur(s['avg_secs'])}")
        lines.append("")
        lines.append(hdr("LAST 7 DAYS"))
        BLOCKS = " ▁▂▃▄▅▆▇█"
        last7 = s["last7"]
        mx = max(last7) or 1
        spark = "".join(BLOCKS[min(8, round(v / mx * 8))] for v in last7)
        # weekday initials aligned under each spark cell (oldest -> newest = ... -> today)
        today = datetime.now().date()
        inits = "".join("MTWTFSS"[(today - timedelta(days=i)).weekday()] for i in range(6, -1, -1))
        lines.append(f"  [{p.accent}]{spark}[/]   [{p.secondary}]{sum(last7)} this week-window[/]")
        lines.append(f"  [{self._muted()}]{inits}[/]  [{self._muted()}](→ today)[/]")
        lines.append("")
        if s["tag_counts"]:
            lines.append(hdr("TOP TAGS"))
            twidth = max(len(t) for t, _ in s["tag_counts"])
            for tag, c in s["tag_counts"]:
                lines.append(f"  [{p.accent}]{tag:<{twidth}}[/]  {c}")
            lines.append("")
        lines.append(hdr("TOP PROJECTS"))
        if s["top_projects"]:
            width = max(len(name) for name, _ in s["top_projects"])
            for name, c in s["top_projects"]:
                bar = "█" * min(c, 20)
                lines.append(f"  {name:<{width}}  [{self.get_color([name])}]{bar}[/] {c}")
        else:
            lines.append(f"  [{self._muted()}]—[/]")
        lines.append("")
        lines.append(hdr("OPEN NOW"))
        lines.append(f"  Open tasks   {s['open']}")
        lines.append(f"  Overdue      [{p.error}]{s['overdue']}[/]")
        lines.append(f"  Due today    [{p.warning}]{s['due_today']}[/]")
        lines.append(f"  Due ≤ 7d     {s['due_week']}")
        lines.append(f"  No due date  {s['no_due']}")
        return "\n".join(lines)

    # ---------- FOLDER VIEW ----------
    def render_folderview(self):
        # Group every open task under its top-level project folder so all the
        # major parents and their tasks are visible at a glance.
        open_tasks = [t for t in self.tasks if not t.completed]
        if not open_tasks:
            return f"[{self._muted()}]// no active tasks — awaiting input[/]"

        groups = {}
        for t in open_tasks:
            top = t.path[0].title() if t.path else "General"
            groups.setdefault(top, []).append(t)

        lines = []
        for top in sorted(groups, key=lambda k: (k == "General", k.lower())):
            color = self.get_color([top])
            tasks = sorted(groups[top], key=lambda t: (t.due or date.max, t.path, t.text))
            lines.append(f"[bold {color}]{top}[/]  "
                         f"[{self._muted()}]· {len(tasks)} task(s)[/]")
            for t in tasks:
                sub = " / ".join(t.path[1:]) if len(t.path) > 1 else ""
                sub_str = f"  [{self._muted()}][{sub}][/]" if sub else ""
                lines.append(
                    f"   [{color}]•[/] {t.text}{sub_str}"
                    + self.due_str(t) + self.cat_str(t)
                )
            lines.append("")
        return "\n".join(lines).rstrip()

    # ---------- HELP ----------
    def render_help(self, topic=None):
        p = self._palette()
        key = HELP_ALIASES.get(topic.strip().lstrip("/").lower()) if topic else None
        lines = []
        if topic and key is None:
            lines.append(f"[{p.error}]No help topic '{topic}' — showing everything.[/]")
            lines.append("")
        sections = ([s for s in HELP_SECTIONS if s[0] == key] if key else HELP_SECTIONS)
        for _skey, title, body in sections:
            lines.append(f"[bold {p.primary}]{title}[/]")
            lines.extend(body)
            lines.append("")
        return "\n".join(lines).rstrip()

    # ---------- CATEGORIES ----------
    def category_legend(self):
        lines = ["TIMESHEET CATEGORIES — use any shortcut:"]
        for code, label, aliases in CATEGORIES:
            lines.append(f"  {' · '.join(aliases):<16} {label}")
        return "\n".join(lines)

    # ---------- SCHEDULE / TIMESHEET ----------
    def render_schedule(self, mode, anchor, view):
        p = self._palette()
        if mode == "week":
            start = anchor - timedelta(days=anchor.weekday())
            end = start + timedelta(days=6)
            title = f"Week of {start.strftime('%b %d')} – {end.strftime('%b %d, %Y')}"
        else:
            start = end = anchor
            title = anchor.strftime("%a %b %d, %Y")

        entries = [e for e in self.time_entries
                   if start <= date.fromisoformat(e["date"]) <= end]
        total = sum(e["minutes"] for e in entries)

        def hrs(m):
            h, mm = divmod(m, 60)
            return f"{h}h {mm:02d}m" if h else f"{mm}m"

        lines = [
            f"[bold {p.primary}]{title}[/]",
            f"[{p.secondary}]{view.upper()} VIEW  ·  total [bold]{hrs(total)}[/][/]",
            "",
        ]
        if not entries:
            lines.append(f"[{self._muted()}]// no time logged in this period[/]")
            return "\n".join(lines)

        # build (label, minutes, color-key) rows for whichever view
        if view == "category":
            totals = Counter()
            for e in entries:
                totals[e["category"] or "untagged"] += e["minutes"]
            rows = [(CATEGORY_SHORT.get(c, "Untagged" if c == "untagged" else c), m, c)
                    for c, m in totals.items()]
        else:  # by task
            totals = Counter()
            for e in entries:
                if e["task"]:
                    totals[e["task"]] += e["minutes"]
            if not totals:
                lines.append(f"[{self._muted()}]// no task-tagged time in this period[/]")
                return "\n".join(lines)
            rows = [((t if len(t) <= 28 else t[:27] + "…"), m, t) for t, m in totals.items()]

        rows.sort(key=lambda r: -r[1])
        mx = max(m for _, m, _ in rows)
        wlbl = max(len(lbl) for lbl, _, _ in rows)
        for label, mins, key in rows:
            color = self.get_color([key])
            bar = "█" * max(1, round(mins / mx * 18))
            pct = round(mins / total * 100)
            lines.append(
                f"  [{color}]{label:<{wlbl}}[/]  "
                f"[{p.foreground}]{hrs(mins):>7}[/]  "
                f"[{color}]{bar}[/] [{self._muted()}]{pct:>2}%[/]"
            )
        return "\n".join(lines)

    # ---------- SAVE / LOAD ----------
    # ---------- UNDO ----------
    UNDO_LIMIT = 100

    def _snapshot(self):
        # value-only picture of all user-editable data (settings excluded)
        return {
            "tasks": [t.to_dict() for t in self.tasks],
            "archived": [t.to_dict() for t in self.archived_tasks],
            "task_id": self.task_id,
            "notes": dict(self.notes),
            "time_entries": [dict(e) for e in self.time_entries],
        }

    def _push_undo(self):
        self._undo_stack.append(self._snapshot())
        if len(self._undo_stack) > self.UNDO_LIMIT:
            self._undo_stack.pop(0)

    def _restore(self, snap):
        self.tasks = [Task.from_dict(t) for t in snap["tasks"]]
        self.archived_tasks = [Task.from_dict(t) for t in snap["archived"]]
        self.task_id = snap["task_id"]
        self.notes = dict(snap["notes"])
        self.time_entries = [dict(e) for e in snap["time_entries"]]
        # any in-flight edit/timer context may now point at stale tasks
        self.editing_task_id = None

    def undo_last(self):
        # restore the most recent snapshot that differs from the current state,
        # so read-only commands (/help, /filter, …) don't waste an undo step
        current = self._snapshot()
        while self._undo_stack:
            snap = self._undo_stack.pop()
            if snap != current:
                self._restore(snap)
                return True
        return False

    def save_data(self):
        data = {
            "tasks": [t.to_dict() for t in self.tasks],
            "archived": [t.to_dict() for t in self.archived_tasks],
            "task_id": self.task_id,
            "sound_on": self.sound_on,
            "theme": self.theme,
            "notes": self.notes,
            "time_entries": self.time_entries,
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
        self.time_entries = data.get("time_entries", [])

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

    # ---------- GRAPH (TILE VIEW) ----------
    @staticmethod
    def _vis(s):
        # visible width of a markup string (tags don't take screen columns)
        return len(MARKUP_RE.sub("", s))

    def _graph_width(self):
        # usable inner width of the left pane (minus border + padding + scrollbar)
        try:
            w = self.query_one("#graph-scroll").size.width
        except Exception:
            w = 0
        if w <= 0:
            w = 60
        return max(24, w - 6)

    def _due_badge(self, t):
        # (plain label, color) for a task's due date, emoji-free for tile alignment
        if not t.due:
            return "", ""
        p = self._palette()
        delta = (t.due - datetime.now().date()).days
        if delta < 0:
            return f"overdue {-delta}d", f"bold {p.error}"
        if delta == 0:
            return "today", f"bold {p.warning}"
        if delta == 1:
            return "tmrw", p.accent
        if delta <= 7:
            return t.due.strftime("%a"), p.secondary
        return t.due.isoformat(), "#6b7a80"

    def _make_tile(self, title, color, tasks, index_map, width):
        # Build one project tile as a list of lines, each exactly `width` columns
        # of *visible* text (markup excluded), so tiles align side by side.
        iw = width - 2  # interior width, between the │ borders
        head = f" {title} ({len(tasks)}) "
        if len(head) > iw:
            head = head[:iw]
        lines = [f"[{color}]┌{head}{'─' * (iw - len(head))}┐[/]"]

        for t in tasks:
            num = index_map[id(t)]
            is_current = t.id == self.current_task_id
            mark = ">" if is_current else " "
            prefix = f"{mark}({num}) "
            label, due_color = self._due_badge(t)
            due = f" ·{label}" if label else ""
            budget = iw - 1  # leave one leading space inside the tile
            room = budget - len(prefix) - len(due)
            if room < 4 and due:           # not enough space — drop the due badge
                due, due_color, room = "", "", budget - len(prefix)
            text = t.text
            if len(text) > room:
                text = text[:max(0, room - 1)] + "…"
            txt_color = f"bold {self._palette().accent}" if is_current else color
            body = f"[{txt_color}]{prefix}{text}[/]"
            if due:
                body += f"[{due_color}]{due}[/]"
            body = " " + body
            body += " " * (iw - self._vis(body))
            lines.append(f"[{color}]│[/]{body}[{color}]│[/]")

        lines.append(f"[{color}]└{'─' * iw}┘[/]")
        return lines

    def _render_tag_links(self, open_tasks, index_map, width):
        # Per-tag connector groups: each #tag fans lines out to every task with it.
        p = self._palette()
        tagmap = {}
        for t in open_tasks:
            for tok in set(t.text.split()):
                if tok.startswith("#") and len(tok) > 1:
                    tagmap.setdefault(tok, []).append(t)
        shared = {tag: ts for tag, ts in tagmap.items() if len(ts) >= 2}
        if not shared:
            return []

        order = sorted(shared, key=lambda tg: (-len(shared[tg]), tg.lower()))
        tw = min(max(len(tg) for tg in order), 18)

        lines = ["", f"[bold {p.primary}]TAG LINKS[/]"]
        for tag in order:
            ts = sorted(shared[tag], key=lambda t: index_map[id(t)])
            tcolor = self.get_color([tag])
            shown = tag if len(tag) <= tw else tag[:tw - 1] + "…"
            for i, t in enumerate(ts):
                pcolor = self.get_color(t.path)
                num = index_map[id(t)]
                lbl = f"({num}) {t.text}"
                room = width - (tw + 6)
                if room > 4 and len(lbl) > room:
                    lbl = lbl[:room - 1] + "…"
                if i == 0:
                    stem = f"{shown:<{tw}} ──┬─" if len(ts) > 1 else f"{shown:<{tw}} ────"
                else:
                    branch = "└─" if i == len(ts) - 1 else "├─"
                    stem = f"{' ' * (tw + 3)}{branch}"
                lines.append(f"[{tcolor}]{stem}[/] [{pcolor}]{lbl}[/]")
        return lines

    def _join_tiles_row(self, tiles, width, gap=2):
        # Stack a row of tiles side by side, padding short ones with blank lines.
        height = max(len(tile) for tile in tiles)
        blank = " " * width
        out = []
        for r in range(height):
            cells = [tile[r] if r < len(tile) else blank for tile in tiles]
            out.append((" " * gap).join(cells))
        return out

    def build_graph(self):
        if self.show_completed:
            # show the completed log in the big left pane so it's prominent
            return self.build_completed_view()

        p = self._palette()
        open_tasks = self.get_open_tasks_ordered()
        index_map = {id(t): i + 1 for i, t in enumerate(open_tasks)}

        lines = []
        if self.filter_tag or self.filter_due:
            lines.append(f"[{p.warning}]Filter: {self.filter_label()}[/] "
                         f"[{self._muted()}](Esc to clear)[/]")

        if not open_tasks:
            lines.append(f"[{self._muted()}]// no active tasks — awaiting input[/]")
            return "\n".join(lines)

        # group open tasks under their top-level project folder
        groups = {}
        for t in open_tasks:
            top = t.path[0].title() if t.path else "General"
            groups.setdefault(top, []).append(t)

        # responsive grid geometry: pack tiles into as many columns as fit
        W = self._graph_width()
        gap, target = 2, 28
        cols = max(1, (W + gap) // (target + gap))
        tile_w = max(16, (W - gap * (cols - 1)) // cols)

        names = sorted(groups, key=lambda k: (k == "General", k.lower()))
        tiles = [
            self._make_tile(name, self.get_color([name]),
                            sorted(groups[name], key=lambda t: index_map[id(t)]),
                            index_map, tile_w)
            for name in names
        ]

        for i in range(0, len(tiles), cols):
            row = tiles[i:i + cols]
            lines.extend(self._join_tiles_row(row, tile_w, gap))
            lines.append("")  # breathing room between tile rows

        lines.extend(self._render_tag_links(open_tasks, index_map, W))
        return "\n".join(lines).rstrip()

    # ---------- TASK VIEW ----------
    def build_task_view(self):
        if self.show_completed:
            p = self._palette()
            return (
                f"[{p.warning}]✓ COMPLETED LOG[/]  [{self._muted()}](shown left ←)[/]\n\n"
                f"[{p.secondary}]{self.get_stats()}[/]\n\n"
                f"[{self._muted()}]Esc (or /back) to return[/]"
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
                + self.due_str(t) + self.cat_str(t)
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
        # Pull out the due date ("@<date>" or "due <date>") and a "+<category>"
        # token (e.g. +build), then parse the rest into text + path.
        due = None
        category = None
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
            if category is None and tok.startswith("+") and len(tok) > 1:
                c = resolve_category(tok[1:])
                if c:
                    category = c
                    i += 1
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

        return text, path, due, category

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
        if t.category:
            parts.append("+" + t.category)
        parts.append(t.text)
        return " ".join(parts)

    def cat_str(self, t):
        # small colored category badge (uses the short code, e.g. •build)
        if not t.category:
            return ""
        return f" [{self.get_color([t.category])}]•{t.category}[/]"

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
        # Faded line above the input; contextual help while typing.
        low = value.lower()
        last = value.split(" ")[-1] if value else ""
        dates = "today · tomorrow · mon–sun · 3d · 2026-06-15"
        cats = "admin · dev · kt · prod · run · plan · build · personal  (or 1–8)"

        # ---- typing a command name (no space yet): show matches + descriptions ----
        if low.startswith("/") and " " not in value:
            matches = [c for c in self.COMMANDS if c.startswith(low)]
            if not matches:
                return "[dim]? unknown command — type /help[/]"
            if len(matches) == 1:
                c = matches[0]
                return f"[dim]{c} — {COMMAND_DESCRIPTIONS.get(c, '')}[/]"
            if low in self.COMMANDS:  # exact, but also a prefix of others
                others = "  ".join(c for c in matches if c != low)
                return f"[dim]{low} — {COMMAND_DESCRIPTIONS.get(low, '')}   · also {others}[/]"
            shown = "  ·  ".join(matches[:6])
            return f"[dim]{shown}{'  …' if len(matches) > 6 else ''}[/]"

        # ---- command contexts ----
        if low.startswith("/track"):
            return f"[dim]categories: {cats} · /categories[/]"
        if re.match(r"/current\s+\d+\s", low):
            return f"[dim]optional: <min> and/or category — {cats}[/]"
        if re.match(r"/done\s+\d+\s", low):
            return "[dim]optional: time to log — 90 · 90m · 1h · 1h30m[/]"
        if low.startswith("/filter due"):
            return "[dim]filter by due: today · tomorrow · week · overdue · 2026-06-15[/]"
        if re.match(r"/edit\s+\d+\s", low) is not None:
            return f"[dim]set due date:  due tomorrow · due fri · due 2026-06-15   (dates: {dates})[/]"
        if low.startswith("/due "):
            return f"[dim]due options: {dates}[/]"
        if low.startswith("/"):
            return ""  # other commands: the / autocomplete handles it

        # ---- writing a task: switch hint to the token being typed ----
        toks = value.split(" ")
        prev = toks[-2].lower() if len(toks) >= 2 else ""
        if last.startswith("@") or prev == "due":
            return f"[dim]due: {dates}[/]"
        if last.startswith("+"):
            return f"[dim]category: {cats}[/]"
        if last.startswith("#"):
            tags = "  ".join(sorted(self.all_tags())[:6])
            return f"[dim]tag: {tags}[/]" if tags else "[dim]tag: #yourlabel[/]"
        if "/" in last and len(last) > 1:
            return "[dim]project: Work/Sub  ·  Tab completes an existing folder[/]"
        # default composition guide (also shown when the box is empty)
        return ("[dim]project Work/Sub  ·  +category  ·  #tag  ·  due @tomorrow"
                "  ·  /help for commands[/]")

    def on_input_changed(self, event: Input.Changed):
        self.hint_bar.update(self.hint_for(event.value))

    # ---------- INPUT ----------
    def on_input_submitted(self, event: Input.Submitted):
        raw = event.value.strip()
        event.input.value = ""

        # snapshot before anything that might change data — but not before
        # /undo itself (it must not record the state it's about to roll back)
        if raw and raw.split()[0].lower() != "/undo":
            self._push_undo()

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
        text, path, due, category = self.parse_task_input(raw)
        if not text:
            return
        task = Task(self.task_id, text, path, due,
                    created_at=datetime.now(), category=category)
        self.tasks.append(task)
        self.task_id += 1
        self._start_reveal(task)        # decrypt-style reveal of the new text
        self.notify("+ NODE ADDED", timeout=2)

    def apply_edit(self, raw):
        task = next((t for t in self.tasks if t.id == self.editing_task_id), None)
        self.editing_task_id = None
        if task is None:
            return

        text, path, due, category = self.parse_task_input(raw)
        if not text:
            self.notify("! EDIT ABORTED (empty)")
            return

        task.category = category
        task.text = text
        task.path = path
        task.due = due
        self.notify("▸ RECORD UPDATED")

    # ---------- COMMANDS ----------
    def handle_command(self, cmd):
        parts = cmd.split()

        if parts[0] == "/current":
            try:
                idx = int(parts[1])
                task = self.get_open_tasks_ordered()[idx - 1]
            except (ValueError, IndexError):
                self.notify("! USAGE: /current <n> [min] [category]")
            else:
                mins, cat = None, None
                for tok in parts[2:]:
                    if tok.isdigit():
                        mins = int(tok)
                    else:
                        c = resolve_category(tok)
                        if c:
                            cat = c
                if cat is None:
                    cat = task.category  # fall back to the task's assigned category
                tag = f" · {CATEGORY_LABEL[cat]}" if cat else ""
                if mins:
                    self._start_countdown(mins, cat, task.id, task.text)
                    self.notify(f"▸ POMODORO {mins}m · {task.text}{tag}")
                else:
                    self._start_countup(cat, task.id, task.text)
                    self.notify(f"▸ TRACKING · {task.text}{tag}")

        elif parts[0] == "/track":
            cat = resolve_category(parts[1]) if len(parts) > 1 else None
            if cat == PERSONAL_CAT:
                self.notify("▸ personal isn't time-tracked")
            elif not cat:
                self.notify("! CATEGORY? admin · dev · kt · prod · run · plan · build  (or 1-7)")
            elif len(parts) > 2 and parts[2].isdigit():
                # log a block of time directly (rounded to 30m), no live timer
                rounded = self._log_time(int(parts[2]), cat, None)
                self.notify(f"▸ LOGGED {rounded}m · {CATEGORY_LABEL[cat]}")
            else:
                # start a live count-up stopwatch for this category
                self._start_countup(cat, None, None)
                self.notify(f"▸ TRACKING · {CATEGORY_LABEL[cat]}")

        elif cmd == "/stop":
            if self.timer_mode:
                self._stop_timer(log=True, announce=True)
                self.current_task_id = None
            else:
                self.notify("! NO ACTIVE TIMER")

        elif cmd == "/undo":
            if self.undo_last():
                self.notify("↶ UNDONE · last action reverted")
            else:
                self.notify("! NOTHING TO UNDO")

        elif cmd == "/schedule":
            self.push_screen(ScheduleScreen())

        elif cmd in ("/categories", "/cats"):
            self.notify(self.category_legend(), timeout=22)

        elif cmd == "/auditcat":
            pending = [t.id for t in self.tasks if not t.completed and not t.category]
            if not pending:
                self.notify("▸ all open tasks already have a category")
            else:
                self.push_screen(AuditCategoriesScreen(pending))

        elif parts[0] == "/cat":
            try:
                idx = int(parts[1])
                task = self.get_open_tasks_ordered()[idx - 1]
            except (ValueError, IndexError):
                self.notify("! USAGE: /cat <n> <category>   (blank = clear)")
            else:
                if len(parts) > 2:
                    c = resolve_category(parts[2])
                    if not c:
                        self.notify("! UNKNOWN CATEGORY — /categories to list")
                    else:
                        task.category = c
                        self.notify(f"▸ CATEGORY · {task.text} → {CATEGORY_LABEL[c]}")
                else:
                    task.category = None
                    self.notify("▸ CATEGORY CLEARED")

        elif parts[0] == "/done" and len(parts) > 1:
            try:
                idx = int(parts[1])
                task = self.get_open_tasks_ordered()[idx - 1]
            except (ValueError, IndexError):
                self.notify("! INVALID NODE")
            else:
                # an optional trailing time logs a timesheet block against this
                # task's category (e.g. /done 2 90 · /done 2 1h30m)
                logged = None
                if len(parts) > 2:
                    mins = self._parse_minutes(" ".join(parts[2:]))
                    if mins is None:
                        self.notify("! BAD TIME — try: /done 2 90 · /done 2 1h30m")
                        return
                    logged = self._log_time(mins, task.category, task.text)

                task.completed = True
                task.completed_at = datetime.now()
                self._play("complete")
                self._start_redact(task)

                # if a timer was tracking this task, stop it — auto-log only when
                # no explicit time was given (an explicit time supersedes it)
                timer_logged = None
                if task.id == self.current_task_id:
                    if self.timer_mode:
                        timer_logged = self._stop_timer(
                            log=logged is None, announce=logged is None)
                    self.current_task_id = None

                # default: closing out a non-personal task with no time recorded
                # (no explicit time, no timer) counts as a 30-minute block
                if logged is None and timer_logged is None and not self.is_personal(task):
                    logged = self._log_time(30, task.category, task.text)

                if logged:
                    self.notify(f"▓ TARGET CLEARED · {task.text}  (+{logged}m logged)")
                else:
                    self.notify(f"▓ TARGET CLEARED · {task.text}")

        elif parts[0] == "/edit" and len(parts) > 1:
            try:
                idx = int(parts[1])
                task = self.get_open_tasks_ordered()[idx - 1]
            except (ValueError, IndexError):
                self.notify("! USAGE: /edit <n>   or   /edit <n> due <date>")
            else:
                if len(parts) > 2:
                    # quick due-date edit — change only the date, leave text alone.
                    # accepts "/edit n due tomorrow" or "/edit n @tomorrow".
                    date_parts = parts[2:]
                    if date_parts[0].lower() == "due":
                        date_parts = date_parts[1:]
                    d = self.parse_due(" ".join(date_parts)) if date_parts else None
                    if d is None:
                        self.notify("! BAD DATE — try: due tomorrow · due fri · 2026-06-15")
                    else:
                        task.due = d
                        self.notify(f"▸ DUE SET · {d.isoformat()}")
                else:
                    self.editing_task_id = task.id
                    self.input_box.value = self.task_to_input(task)
                    self.input_box.cursor_position = len(self.input_box.value)
                    self.input_box.focus()
                    self.notify(f"▸ EDITING NODE {idx} — edit & Enter")

        elif parts[0] == "/delete" and len(parts) > 1:
            try:
                idx = int(parts[1])
                task = self.get_open_tasks_ordered()[idx - 1]
                self.tasks.remove(task)
                if task.id == self.current_task_id:
                    self._stop_timer(log=False)  # don't bill a deleted task
                    self.current_task_id = None
                if task.id == self.editing_task_id:
                    self.editing_task_id = None
                self.notify(f"✗ PURGED · {task.text}")
            except (ValueError, IndexError):
                self.notify("! USAGE: /delete <n>")

        elif parts[0] == "/due" and len(parts) > 1:
            try:
                idx = int(parts[1])
                task = self.get_open_tasks_ordered()[idx - 1]
            except (ValueError, IndexError):
                self.notify("! USAGE: /due <n> <date>   (no date = clear)")
            else:
                if len(parts) > 2:
                    d = self.parse_due(parts[2])
                    if d is None:
                        self.notify("! BAD DATE — try: 2026-06-15 · @fri · @3d")
                    else:
                        task.due = d
                        self.notify(f"▸ DUE SET · {d.isoformat()}")
                else:
                    task.due = None
                    self.notify("▸ DUE CLEARED")

        elif parts[0] == "/filter":
            if len(parts) >= 2 and parts[1].lower() == "due":
                if len(parts) >= 3:
                    self.filter_due = self.normalize_due_filter(parts[2])
                    if self.filter_due is None:
                        self.notify("Due filter: today | tomorrow | week | overdue | YYYY-MM-DD")
                    else:
                        self.filter_tag = None
                        self.notify(f"▸ FILTER · {self.filter_label()}")
                else:
                    self.filter_due = None
                    self.notify("▸ FILTER CLEARED")
            elif len(parts) > 1:
                self.filter_tag = parts[1].lstrip("#")
                self.filter_due = None
                self.notify(f"▸ FILTER · #{self.filter_tag}")
            else:
                self.filter_tag = None
                self.filter_due = None
                self.notify("▸ FILTER CLEARED")

        elif cmd == "/stats":
            self.push_screen(StatsScreen())

        elif cmd in ("/folderview", "/folders"):
            self.push_screen(FolderViewScreen())

        elif cmd == "/completed":
            self.show_completed = True

        elif cmd == "/back":
            self.show_completed = False

        elif cmd in ("/clearcomplete", "/clearcompleted"):
            # archive every completed task now (instead of waiting for the 24h
            # auto-archive) so they drop out of the active view but stay in the
            # /completed log and stats. Undoable via /undo.
            done = [t for t in self.tasks if t.completed]
            if not done:
                self.notify("▸ no completed tasks to clear")
            else:
                self.archived_tasks.extend(done)
                self.tasks = [t for t in self.tasks if not t.completed]
                self.export_readable_report()
                self.notify(f"✓ CLEARED {len(done)} completed task(s)")

        elif cmd == "/clear all":
            self.push_screen(PurgeScreen())  # splash, then _do_clear_all()

        elif cmd == "/export":
            self.export_readable_report()
            self.notify("▸ REPORT EXPORTED")

        elif cmd == "/sound":
            self.sound_on = not self.sound_on
            self.notify(f"▸ AUDIO {'ON' if self.sound_on else 'OFF'}")
            self._play("toggle")

        elif parts[0] in ("/notes", "/note"):
            if len(parts) > 1:
                path = self._normalize_note_path(" ".join(parts[1:]))
                if path:
                    self.push_screen(NotesScreen(path))
                else:
                    self.notify("! USAGE: /notes <folder/path>   (or /notes to browse)")
            else:
                self.push_screen(NotesBrowserScreen())

        elif parts[0] == "/theme":
            if len(parts) > 1:
                name = parts[1].lower()
                if name in self.available_themes:
                    self.theme = name
                    self._theme_name = name
                    self.notify(f"▸ THEME · {name}")
                else:
                    self.notify(f"! UNKNOWN THEME '{name}' — type /theme to list")
            else:
                names = "  ".join(sorted(self.available_themes))
                self.notify(f"Current: {self.theme}\nThemes: {names}\nUse /theme <name>", timeout=15)

        elif parts[0] == "/help":
            self.push_screen(HelpScreen(parts[1] if len(parts) > 1 else None))

    # ---------- REFRESH ----------
    def _do_clear_all(self):
        # actual wipe, run by PurgeScreen after the splash (keeps timesheet log)
        self._stop_timer(log=False)
        self.tasks.clear()
        self.archived_tasks.clear()
        self.current_task_id = None
        self.editing_task_id = None
        self.filter_tag = None
        self.filter_due = None
        self.save_data()
        self.refresh_all()

    def go_back(self):
        # Esc on the main screen: leave the completed view, else clear a filter,
        # else cancel an edit / clear the input. Returns True if it did something.
        if self.show_completed:
            self.show_completed = False
            self.refresh_all()
            return True
        if self.filter_tag or self.filter_due:
            self.filter_tag = None
            self.filter_due = None
            self.notify("▸ FILTER CLEARED")
            self.refresh_all()
            return True
        if self.input_box.value:
            self.input_box.value = ""
            self.editing_task_id = None
            return True
        return False

    def on_resize(self, event):
        # tiles are laid out to the pane's width, so reflow them on resize
        try:
            self.graph_view.update(self.build_graph())
        except Exception:
            pass

    def refresh_all(self):
        self.graph_view.update(self.build_graph())
        self.task_view.update(self.build_task_view())
        self.status_bar.update(self.render_big_timer())


# ---------- RUN ----------
if __name__ == "__main__":
    TodoApp().run()
