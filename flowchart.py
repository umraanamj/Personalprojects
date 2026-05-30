"""
flowchart.py — Obsidian-style graph of your tasks, in the terminal.

Reads ~/TodoApp/data.json (the file ToDo.py writes) and draws how tasks
interconnect through shared #hashtags. Each #tag is a hub; the tasks that
carry it are its spokes. Tasks that share more than one tag are flagged as
bridges between clusters.

    python3 flowchart.py            # open + completed tasks
    python3 flowchart.py --all      # include archived too

Uses `rich` if available (colors), falls back to plain text otherwise.
"""

import json
import os
import re
import sys
from collections import defaultdict

DATA_FILE = os.path.join(os.path.expanduser("~"), "TodoApp", "data.json")

try:
    from rich.console import Console
    _c = Console()
    def out(m=""):
        _c.print(m)
except ImportError:
    def out(m=""):
        print(re.sub(r"\[/?[^\]]*\]", "", str(m)))


TAG_RE = re.compile(r"#\w+")

# A small rotating palette so each tag cluster reads as its own color.
PALETTE = ["cyan", "magenta", "green", "yellow", "blue", "red",
           "bright_cyan", "bright_magenta", "bright_green", "bright_yellow"]


def load_tasks(include_archived):
    if not os.path.exists(DATA_FILE):
        out(f"[red]No data file at {DATA_FILE}[/]")
        out("[dim]Add a few tasks in ToDo.py first.[/]")
        sys.exit(1)
    with open(DATA_FILE) as f:
        data = json.load(f)
    tasks = list(data.get("tasks", []))
    if include_archived:
        tasks += data.get("archived", [])
    return tasks


def tags_of(task):
    return set(TAG_RE.findall(task["text"]))


def main():
    include_archived = "--all" in sys.argv
    tasks = load_tasks(include_archived)

    # tag -> [tasks],  and remember each task's tags for bridge detection
    tag_to_tasks = defaultdict(list)
    for t in tasks:
        for tag in tags_of(t):
            tag_to_tasks[tag].append(t)

    tag_color = {tag: PALETTE[i % len(PALETTE)]
                 for i, tag in enumerate(sorted(tag_to_tasks))}

    out()
    out("[bold]Task graph — connected by #hashtags[/]")
    out(f"[dim]{len(tasks)} tasks · {len(tag_to_tasks)} tags · "
        f"source: {DATA_FILE}[/]")

    if not tag_to_tasks:
        out("\n[yellow]No #hashtags found in any task.[/]")
        out("[dim]Tag tasks like:  buy milk #groceries[/]")
        return

    # ---- each tag as a hub with its tasks as spokes ----
    for tag in sorted(tag_to_tasks, key=lambda t: (-len(tag_to_tasks[t]), t)):
        members = tag_to_tasks[tag]
        color = tag_color[tag]
        out()
        out(f"[bold {color}]◉ {tag}[/] [dim]({len(members)} "
            f"task{'s' if len(members) != 1 else ''})[/]")

        for i, t in enumerate(members):
            last = i == len(members) - 1
            connector = "└──" if last else "├──"

            status = "[green]✓[/]" if t.get("completed") else "[dim]○[/]"
            path = " / ".join(t.get("path") or []) or "General"

            # other tags this task also carries -> it bridges clusters
            others = tags_of(t) - {tag}
            bridge = ""
            if others:
                links = " ".join(f"[{tag_color.get(o, 'white')}]{o}[/]"
                                 for o in sorted(others))
                bridge = f"  [grey50]↔ {links}[/]"

            text = t["text"]
            out(f"  [{color}]{connector}[/] {status} {text} "
                f"[grey50][{path}][/]{bridge}")

    # ---- bridges: tasks tying multiple clusters together ----
    bridges = [t for t in tasks if len(tags_of(t)) > 1]
    if bridges:
        out()
        out("[bold]🔗 Bridges[/] [dim](tasks linking multiple tags)[/]")
        for t in bridges:
            links = "  ".join(f"[{tag_color[tag]}]{tag}[/]"
                              for tag in sorted(tags_of(t)))
            out(f"  [white]{t['text']}[/]: {links}")

    # ---- orphans: no tags, nothing to connect to ----
    orphans = [t for t in tasks if not tags_of(t)]
    if orphans:
        out()
        out(f"[bold grey50]· Untagged[/] [dim]({len(orphans)} "
            f"floating, no connections)[/]")
        for t in orphans:
            status = "[green]✓[/]" if t.get("completed") else "[dim]○[/]"
            out(f"  [grey50]·[/] {status} [grey50]{t['text']}[/]")

    out()


if __name__ == "__main__":
    main()
