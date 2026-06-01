from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Input, Static
from textual.containers import Horizontal, VerticalScroll
from textual.suggester import Suggester
from datetime import datetime, timedelta, date
import hashlib
import json
import os
import re

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


# ---------- APP ----------
class TodoApp(App):
    COMMANDS = [
        "/help", "/current", "/done", "/edit", "/due", "/delete", "/filter",
        "/completed", "/back", "/clear all", "/export",
    ]

    CSS = """
    Screen { layout: vertical; }

    /* main panes take all the flexible space, pushing the rest to the bottom */
    #main { height: 1fr; }

    /* scrollable wrappers hold the border/width; inner Static grows to fit so
       the wrapper can scroll when there are more tasks than fit on screen */
    #graph-scroll { width: 65%; border: solid #666; height: 1fr; }
    #tasks-scroll { width: 35%; border: solid #666; height: 1fr; }
    #graph, #tasks { padding: 1; height: auto; }

    /* status bar: big pomodoro timer + loading bar + stats (not docked) */
    #status {
        height: 7;
        border-top: solid #666;
        padding: 0 1;
    }

    /* faded contextual hint line, sits just above the input */
    #hint { height: 1; padding: 0 1; }

    /* input sits just above the footer in normal flow */
    #cmd { height: 3; }
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
            placeholder="Type task or command...",
            suggester=InputSuggester(self),
            id="cmd"
        )
        yield self.input_box
        yield Footer()

    def on_mount(self):
        self.load_data()
        self.refresh_all()
        self.set_interval(30, self.cleanup_completed_tasks)
        self.set_interval(1, self.update_timer)

    # ---------- TIMER ----------
    def update_timer(self):
        if not self.timer_end:
            return
        if datetime.now() >= self.timer_end:
            self.timer_end = None
            self.timer_total = None
            self.current_task_id = None
            self.notify("⏱ Timer complete")
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

        if prog:
            remaining, total, frac = prog
            rem_frac = remaining / total if total else 0
            color = "#00d36a" if rem_frac > 0.5 else ("#ffb300" if rem_frac > 0.2 else "#ff4040")
        else:
            frac = 0.0
            color = "#3a8f5f"

        rows = ["", "", ""]
        for ch in txt:
            glyph = BIG.get(ch, BIG[" "])
            for r in range(3):
                rows[r] += glyph[r] + " "
        big = "\n".join(f"[{color}]{row}[/]" for row in rows)

        # Full-width loading bar spanning the status bar.
        bar_w = self.status_bar.size.width - 2
        if bar_w < 10:
            bar_w = 40
        filled = int(round(frac * bar_w))
        bar = "█" * filled + "░" * (bar_w - filled)

        if prog:
            info = f"[{color}]{int(frac * 100)}% elapsed[/]   [bold]{self.get_stats()}[/]"
        else:
            info = f"[bold]{self.get_stats()}[/]   [dim]· /current <n> <min> to start a timer[/]"

        return f"{big}\n[{color}]{bar}[/]\n{info}"

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
            "task_id": self.task_id
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
            return "#dddddd"

        base = int(hashlib.md5(path[0].encode()).hexdigest(), 16)
        r = (base >> 16) & 255
        g = (base >> 8) & 255
        b = base & 255

        # normalize + boost contrast
        def boost(x):
            return min(255, int(100 + (x / 255) * 155))

        r, g, b = boost(r), boost(g), boost(b)
        return f"#{r:02x}{g:02x}{b:02x}"

    # ---------- TAGS / FILTER ----------
    def all_tags(self):
        tags = set()
        for t in self.tasks + self.archived_tasks:
            for tok in t.text.split():
                if tok.startswith("#") and len(tok) > 1:
                    tags.add(tok)
        return tags

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
            return "[gray]Viewing completed → /back[/]"

        tree = {}
        for t in self.visible_tasks():
            node = tree
            for p in t.path:
                if p.strip():
                    node = node.setdefault(p.title(), {})
            node.setdefault("_tasks", []).append(t)

        open_tasks = self.get_open_tasks_ordered()
        index_map = {id(t): i + 1 for i, t in enumerate(open_tasks)}

        lines = []
        if self.filter_tag or self.filter_due:
            lines.append(f"[yellow]Filter: {self.filter_label()} (/filter to clear)[/]")

        def walk(node, prefix="", path=None):
            path = path or []
            keys = [k for k in node if k != "_tasks"]

            for i, k in enumerate(keys):
                connector = "└── " if i == len(keys) - 1 else "├── "
                color = self.get_color(path + [k])
                lines.append(f"{prefix}{connector}[{color}]{k}[/]")
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
                        style = "bold green"
                    elif self.current_task_id and not t.completed:
                        style = "dim"

                    prefix_mark = "👉 " if is_current else ""
                    lines.append(
                        f"{prefix}{connector}[{style}]"
                        f"{prefix_mark}({display_id}) "
                        f"{'✓ ' if t.completed else ''}{t.text}[/]"
                        + self.due_str(t)
                    )

        walk(tree)
        return "\n".join(lines)

    # ---------- TASK VIEW ----------
    def build_task_view(self):
        if self.show_completed:
            return self.build_completed_view()

        open_tasks = self.get_open_tasks_ordered()

        lines = []
        if self.filter_tag or self.filter_due:
            lines.append(f"[yellow]Filter: {self.filter_label()}[/]")

        for i, t in enumerate(open_tasks, start=1):
            is_current = t.id == self.current_task_id
            base_color = self.get_color(t.path)

            if is_current:
                style = f"bold {base_color}"
                prefix = "👉 "
            else:
                style = f"{base_color}"
                prefix = ""

            path = " / ".join(t.path) if t.path else "General"
            lines.append(
                f"[{style}]{prefix}({i}) {t.text}[/] "
                f"[{base_color}][{path}][/]"
                + self.due_str(t)
            )

        if not lines:
            return "[gray]No tasks[/]"
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
        for day in sorted(grouped.keys(), reverse=True):
            lines.append(f"[yellow]{day}[/]")
            for t in grouped[day]:
                lines.append(f"  ✓ {t.text}")
            lines.append("")

        return "\n".join(lines) if lines else "[gray]No completed tasks[/]"

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
        # Pull out the first @token that parses as a due date, then parse the rest.
        due = None
        kept = []
        for tok in raw.strip().split():
            if due is None and tok.startswith("@") and len(tok) > 1:
                parsed = self.parse_due(tok)
                if parsed is not None:
                    due = parsed
                    continue
            kept.append(tok)

        text, path = "", []
        # Leading path (how /edit lays tasks out): first token is a folder path
        # when it contains "/" and there's still text after it.
        if len(kept) >= 2 and "/" in kept[0]:
            path = [p.strip().title() for p in kept[0].split("/") if p.strip()]
            text = " ".join(kept[1:])
        else:
            raw = " ".join(kept).rstrip("/")
            text = raw
            if "/" in raw:
                parts = raw.rsplit(" ", 1)
                if len(parts) == 2 and "/" in parts[1]:
                    text = parts[0]
                    path = [p.strip().title() for p in parts[1].split("/") if p.strip()]

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
        delta = (t.due - datetime.now().date()).days
        if delta < 0:
            color, label = "bold red", f"overdue {-delta}d"
        elif delta == 0:
            color, label = "bold yellow", "today"
        elif delta == 1:
            color, label = "yellow", "tomorrow"
        elif delta <= 7:
            color, label = "#ffaa00", t.due.strftime("%a")
        else:
            color, label = "#888888", t.due.isoformat()
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
        self.tasks.append(Task(self.task_id, text, path, due))
        self.task_id += 1

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
            except (ValueError, IndexError):
                self.notify("Usage: /current <n> [minutes]")

        elif parts[0] == "/done" and len(parts) > 1:
            try:
                idx = int(parts[1])
                task = self.get_open_tasks_ordered()[idx - 1]
                task.completed = True
                task.completed_at = datetime.now()

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

        elif cmd == "/help":
            self.notify(
                "ADD TASK: text, optional 'project/sub', optional '@due'\n"
                "          e.g.  pay rent #bills Money/Home @2026-06-01\n"
                "  due forms: @today @tomorrow @fri @3d @2026-06-01\n"
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
