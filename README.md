# Personalprojects

A collection of personal projects. The main one is **Terminal To-Do** ([`ToDo.py`](ToDo.py)).

---

# Terminal To-Do

A fast, keyboard-driven to-do app that runs entirely in your terminal, built with
[Textual](https://textual.textualize.io/). It organizes tasks into a colored project
tree, understands natural-language due dates, and has a built-in Pomodoro timer with
completion stats — all driven from a single command line at the bottom of the screen.

```
┌─ Projects ──────────────────────────────┐┌─ Tasks ──────────────────┐
│ ├── Money                                ││ (1) pay rent ⏳today      │
│ │   └── (1) pay rent #bills ⏳today      ││ (2) email Sam ⏳tomorrow  │
│ └── Work                                 ││ (3) write spec [Work]     │
│     ├── 👉 (2) email Sam ⏳tomorrow      ││                           │
│     └── (3) write spec                   ││                           │
└──────────────────────────────────────────┘└───────────────────────────┘
 ▌▌ 23:14   ███████████░░░░░░  41% elapsed   Today 3 · Week 12 · Month 30 …
 due options: today · tomorrow · mon–sun · 3d · 2026-06-15
 > _
```

## Features

- **Project tree** — group tasks into nested folders with `Project/Sub` paths. The
  left pane renders them as a tree, each top-level project gets its own auto-assigned
  color, and the right pane shows a flat numbered list.
- **Natural-language due dates** — `@today`, `@tomorrow`, a weekday (`@fri`,
  `@monday`), a relative offset (`@3d`), or an explicit date (`@2026-06-15`, `@06-15`).
  The `@` is optional — you can also write `due today`, `due fri`, etc.
  Tasks show a colored badge that updates live: red **overdue Nd**, bold-yellow
  **today**, yellow **tomorrow**, the weekday name within a week, or the date beyond
  that. Tasks are always ordered soonest-due first.
- **Tags** — add `#hashtags` anywhere in a task and filter by them.
- **Filtering** — by tag (`/filter #work`) or by due date
  (`/filter due today|tomorrow|week|overdue|<date>`). The active filter is shown in
  both panes.
- **Pomodoro & stopwatch** — `/current <n> <minutes>` runs a countdown; `/current <n>`
  (no minutes) runs a **count-up stopwatch** that tracks how long a task takes. A large
  seven-segment clock and a full-width bar fill the status bar.
- **Time tracking / timesheets** — tag any timer to a category and log the time
  (always **rounded up to 30 min**): `/current <n> <cat>`, `/current <n> <min> <cat>`,
  `/track <cat>` to start a category stopwatch, or `/track <cat> <min>` to log a block
  of time directly (e.g. `/track 1 60`). `/stop` ends the active
  timer and logs it; finishing a tracked task logs it too. Categories: `admin`, `dev`,
  `kt`, `prod`, `run`, `plan`, `build` (or `1`–`7`). `/schedule` opens a **timesheet**:
  pick day/week (←/→ to move, `d`/`w` to switch) and toggle (`t`) between a by-category
  and a by-task breakdown, each with hours and bars.
  Tasks can carry a **default category** so timing them is one keystroke: assign with
  `/cat <n> <category>` or by adding a `+build` token to the task text, then `/current <n>`
  auto-tags the time. Tagged tasks show a small `•build` badge.
- **Completion stats** — the status bar tracks tasks completed today, this week, this
  month, this year, and in total. `/stats` opens a full **productivity dashboard**:
  most-productive day this week/month/year, best day ever, top month, busiest weekday,
  current & longest streaks, daily average, completion rate, average time-to-complete,
  a 7-day activity sparkline, top tags, top projects, and an open-task breakdown
  (overdue / due today / due ≤7d / no date).
- **Subtle sounds** — short system sounds on "win" moments (completing a task, finishing
  a Pomodoro, starting a focus timer). Cross-platform with no dependencies (macOS
  `afplay`, Windows `winsound`, Linux freedesktop sounds); toggle with `/sound`.
- **Folder notes** — keep a free-form notes page per folder, independent of tasks.
  `/notes <folder/path>` opens a full-screen editor; `/notes` (no path) opens a
  searchable browser of all your notes. A folder can hold a note with no tasks (it
  won't clutter the task views), and task-folders that have a note show a 📝 marker.
- **Color themes** — switch the whole UI with `/theme <name>`. Ships a custom
  Cyberpunk-2077 theme (default) and a Watch Dogs ctOS theme (`/theme ctos`), plus
  every built-in Textual palette: `dracula`,
  `gruvbox`, `nord`, `catppuccin-mocha`, `rose-pine`, `tokyo-night`, `monokai`,
  `solarized-dark`, and more. Your choice is remembered.
- **Completed view & auto-archive** — `/completed` shows finished tasks grouped by day;
  completed tasks are automatically archived out of the main view after 24 hours.
- **Autocomplete** — type `/` for commands, `#` for existing tags, `@` for due
  dates, or a project path to complete an existing folder one segment at a time
  (e.g. `Wo`→`Work/`, then `Work/Ba`→`Work/Backend`). Accept the faded suggestion
  with **Tab** or **→**. A contextual hint line above the input shows valid options
  for whatever you're typing.
- **Scrollable panes** — both the project tree and the task list scroll with the mouse
  wheel when you have more tasks than fit on screen.
- **Persistent & exportable** — everything is saved to JSON automatically, and a
  human-readable report of completed tasks can be exported to a text file.

## Install & run

Requires Python 3 and the `textual` package:

```bash
pip install textual
python3 ToDo.py
```

## Adding a task

Just type the task and press **Enter**. Optionally tag it, put it in a project, and
give it a due date — order is flexible:

```
pay rent #bills Money/Home @2026-06-15
```

- **`#bills`** — a tag (any number, anywhere in the text)
- **`Money/Home`** — the project path (`Project/Sub/...`), placed at the end
- **`@2026-06-15`** — the due date (see the date forms above)

## Commands

All commands are typed into the input box. `<n>` refers to a task's number in the list.

| Command | What it does |
| --- | --- |
| `/cat <n> <category>` | Assign a default category to a task (blank clears it); or add `+build` in the task text |
| `/auditcat` | Step through every uncategorized task and assign each by pressing `1`–`7` |
| `/current <n> [min] [cat]` | Stopwatch (no min) or pomodoro (min) on a task; uses the task's category if none given |
| `/track <cat>` | Start a live stopwatch for a category (no task) |
| `/track <cat> <min>` | Log `<min>` minutes to a category directly, rounded to 30 (e.g. `/track 1 60`) |
| `/categories` | List all timesheet categories and their shortcuts |
| `/stop` | Stop the active timer and log it (rounded up to 30 min) |
| `/schedule` | Timesheet: day/week breakdown by category or task |
| `/done <n>` | Mark task `n` complete |
| `/edit <n>` | Load task `n` back into the input box to edit (path shown first, so the text sits under the cursor) |
| `/edit <n> due <date>` | Quickly change **only** the due date (`@<date>` also works) |
| `/due <n> [date]` | Set or clear a task's due date (no date = clear) |
| `/delete <n>` | Delete task `n` |
| `/filter <tag>` | Show only tasks with `#tag` (no tag = clear) |
| `/filter due <when>` | Filter by due date: `today`, `tomorrow`, `week`, `overdue`, or a date |
| `/stats` | Open the productivity dashboard |
| `/completed` | View completed tasks, grouped by day |
| `/back` | Leave the completed view |
| `/clear all` | Delete all tasks and the archive |
| `/export` | Write a readable report of completed tasks |
| `/notes [folder/path]` | Edit a folder's note; no path opens the searchable notes browser |
| `/sound` | Toggle completion/timer sounds on or off (persists) |
| `/theme [name]` | Switch color theme; no name lists them all (e.g. `/theme dracula`) |
| `/help [topic]` | Scrollable in-app help; pass a topic to jump to it (e.g. `/help schedule`, `/help time`) |

## Where your data lives

Everything is stored under `~/TodoApp/`:

- `data.json` — all active and archived tasks (saved automatically on every change)
- `completed_report.txt` — the exported readable report of completed tasks
