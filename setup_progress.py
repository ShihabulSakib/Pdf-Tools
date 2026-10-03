#!/usr/bin/env python3
"""
setup_progress.py -- live progress renderer for setup.sh / setup.ps1.

Reads newline-delimited JSON events on stdin and paints them with `rich`
(falls back to `tqdm`, then to plain text).  The calling shell/PowerShell
script owns all real work; this process only draws.

Event protocol (one JSON object per line)
-----------------------------------------
    {"op":"task_add",  "id":"sys", "label":"System packages"}
    {"op":"task_start","id":"sys", "detail":"apt-get install qpdf ghostscript"}
    {"op":"task_log",  "id":"sys", "line":"Reading package lists..."}
    {"op":"task_done", "id":"sys"}
    {"op":"task_fail", "id":"sys", "detail":"exit 100"}
    {"op":"task_skip", "id":"sys", "detail":"nothing to do"}
    {"op":"note",      "level":"info|ok|warn|err|step", "msg":"..."}
    {"op":"done"}

Rules
-----
* Never raises and never exits non-zero.  If this process dies the caller
  simply loses decoration, not functionality.
* Unknown ops and malformed lines are ignored.
* Output for a task is buffered; the most recent line becomes that task's
  dim "detail" text, and the tail is printed only if the task fails.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import sys
import threading

MAX_TAIL = 15          # output lines kept per task (shown if the task fails)
DETAIL_WIDTH = 70      # max chars of live detail text

STATE_PENDING = "pending"
STATE_RUNNING = "running"
STATE_DONE = "done"
STATE_FAILED = "failed"
STATE_SKIPPED = "skipped"


def shorten(text, width=DETAIL_WIDTH):
    """Collapse whitespace and clip to `width`, keeping the informative tail."""
    text = " ".join((text or "").split())
    if len(text) > width:
        text = "\u2026" + text[-(width - 1):]
    return text


def trim_output(text, max_lines=MAX_TAIL):
    """Keep only the last `max_lines` of a chunk of captured command output."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    return lines[-max_lines:]


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------
class PlainBackend:
    """Last-resort renderer: one line per state transition."""

    name = "plain"

    ICON = {
        STATE_PENDING: "[ .. ]",
        STATE_RUNNING: "[>>>]",
        STATE_DONE: "[ OK]",
        STATE_FAILED: "[FAIL]",
        STATE_SKIPPED: "[SKIP]",
    }

    def __init__(self):
        self._state = {}
        self._tails = {}
        self._labels = {}
        self._details = {}
        self._frame = 0
        self._line_length = 0

    # -- task lifecycle ------------------------------------------------------
    def task_add(self, tid, label):
        self._state[tid] = STATE_PENDING
        self._labels[tid] = label
        self._details.setdefault(tid, "")
        self._tails.setdefault(tid, [])
        self._show(tid, "")

    def task_start(self, tid, detail=""):
        self._state[tid] = STATE_RUNNING
        if detail:
            self._details[tid] = shorten(detail)
        if sys.stdout.isatty():
            self._show_running(tid)
        else:
            self._show(tid, "")

    def task_log(self, tid, line):
        tail = self._tails.setdefault(tid, [])
        tail.extend(trim_output(line, 1))
        del tail[:-MAX_TAIL]
        if self._state.get(tid) == STATE_RUNNING:
            self._details[tid] = shorten(line)
            if sys.stdout.isatty():
                self._show_running(tid)

    def _finish(self, tid, state, detail=""):
        self._state[tid] = state
        if detail:
            self._details[tid] = shorten(detail)
        self._show(tid, "")
        if state == STATE_FAILED:
            for ln in self._tails.get(tid, []):
                print("        | %s" % shorten(ln, 100))

    def task_done(self, tid, detail=""):
        self._finish(tid, STATE_DONE, detail)

    def task_fail(self, tid, detail=""):
        self._finish(tid, STATE_FAILED, detail)

    def task_skip(self, tid, detail=""):
        self._finish(tid, STATE_SKIPPED, detail)

    def _show(self, tid, detail):
        icon = self.ICON.get(self._state.get(tid), "[ .. ]")
        label = self._labels.get(tid, tid)
        detail = shorten(detail)
        line = "%s %s" % (icon, label)
        if detail:
            line += "  \u2014 %s" % detail
        if sys.stdout.isatty() and self._line_length:
            print("\r" + (" " * self._line_length) + "\r", end="")
            self._line_length = 0
        print(line, flush=True)

    def _show_running(self, tid):
        width = max(5, min(20, shutil.get_terminal_size((80, 24)).columns - 50))
        offset = self._frame % (width * 2)
        position = offset if offset < width else width * 2 - offset - 1
        bar = ["-"] * width
        bar[position] = "#"
        line = "[>>>] %s [%s]" % (self._labels.get(tid, tid), "".join(bar))
        detail = self._details.get(tid, "")
        if detail:
            line += "  \u2014 %s" % detail
        line = line[:shutil.get_terminal_size((80, 24)).columns]
        print("\r" + line.ljust(self._line_length), end="", flush=True)
        self._line_length = len(line)

    def tick(self):
        if not sys.stdout.isatty():
            return
        running = [tid for tid, state in self._state.items() if state == STATE_RUNNING]
        if running:
            self._frame += 1
            self._show_running(running[-1])

    # -- misc ----------------------------------------------------------------
    def note(self, level, msg):
        tag = {"warn": "WARN ", "err": "ERROR", "step": "==>  "}.get(level, "INFO ")
        if sys.stdout.isatty() and self._line_length:
            print("\r" + (" " * self._line_length) + "\r", end="")
            self._line_length = 0
        print("  %s %s" % (tag, shorten(msg, 160)), flush=True)

    def close(self):
        if self._line_length:
            print(flush=True)
        sys.stdout.flush()


class TqdmBackend:
    """tqdm fallback for hosts without rich."""

    name = "tqdm"

    def __init__(self):
        from tqdm import tqdm

        self._tqdm = tqdm
        self._bars = {}
        self._desc = {}
        self._pos = 0
        self._active = set()
        self._frame = 0

    def _bar(self, tid):
        bar = self._bars.get(tid)
        if bar is None:
            bar = self._tqdm(total=None, position=self._pos, leave=True,
                             bar_format="{desc}  {bar:10}| {elapsed}",
                             ncols=88)
            self._pos += 1
            self._bars[tid] = bar
        return bar

    def _set_desc(self, tid, text):
        if text:
            self._desc[tid] = shorten(text, 44)
        self._bar(tid).set_description("  >>> %s" % self._desc.get(tid, ""))

    def task_add(self, tid, label):
        self._desc.setdefault(tid, shorten(label, 44))
        self._bar(tid).set_description("  ..  %s" % shorten(label, 40))

    def task_start(self, tid, detail=""):
        self._set_desc(tid, detail)
        self._active.add(tid)
        self._bar(tid).refresh()

    def task_log(self, tid, line):
        self._set_desc(tid, line)
        self._bar(tid).refresh()

    def _finish(self, tid, desc):
        bar = self._bar(tid)
        bar.set_description("  %s  %s" % (desc, self._desc.get(tid, "")))
        self._active.discard(tid)
        bar.total = 1
        bar.n = 1
        bar.refresh()
        bar.close()

    def task_done(self, tid, detail=""):
        if detail:
            self._desc[tid] = detail
        self._finish(tid, "  OK  ")

    def task_fail(self, tid, detail=""):
        if detail:
            self._desc[tid] = detail
        self._finish(tid, "FAIL ")

    def task_skip(self, tid, detail=""):
        if detail:
            self._desc[tid] = detail
        self._finish(tid, "SKIP ")

    def tick(self):
        if not self._active:
            return
        self._frame += 1
        frame = "|/-\\"[self._frame % 4]
        for tid in self._active:
            self._bar(tid).set_description("  %s  %s" % (frame, self._desc.get(tid, "")))
            self._bar(tid).refresh()

    def note(self, level, msg):
        sys.stderr.write("  %s\n" % shorten(msg, 200))
        sys.stderr.flush()

    def close(self):
        for bar in self._bars.values():
            try:
                bar.close()
            except Exception:
                pass
        sys.stdout.flush()


def _make_state_column():
    """A ProgressColumn that animates ONLY while a task is actually running.

    rich's stock SpinnerColumn animates every unfinished row, which makes
    queued tasks look busy.  This one shows a dim dot while queued and a
    static glyph once finished.
    """
    from rich.progress import ProgressColumn
    from rich.spinner import Spinner
    from rich.text import Text

    glyphs = {
        STATE_PENDING: ("\u25cb", "grey50"),
        STATE_DONE: ("\u2714", "bold green"),
        STATE_FAILED: ("\u2716", "bold red"),
        STATE_SKIPPED: ("\u2013", "yellow"),
    }

    class StateColumn(ProgressColumn):
        def __init__(self):
            self._spinner = Spinner("dots", style="cyan")
            super().__init__()

        def render(self, task):
            state = task.fields.get("state", STATE_RUNNING)
            if state == STATE_RUNNING:
                return self._spinner.render(task.get_time())
            glyph, colour = glyphs.get(state, glyphs[STATE_PENDING])
            return Text(glyph, style=colour)

    return StateColumn()


def _shorten_to(text, width):
    """Clip to `width`, keeping the informative tail (paths, exit messages)."""
    text = " ".join((text or "").split())
    if len(text) > width:
        text = "\u2026" + text[-(width - 1):] if width > 1 else "\u2026"
    return text


def _head_to(text, width):
    """Clip to `width`, keeping the head (labels read better left-aligned)."""
    text = " ".join((text or "").split())
    if len(text) > width:
        text = text[:width - 1] + "\u2026" if width > 1 else "\u2026"
    return text


class RichBackend:
    """Preferred renderer: animated spinners + per-task bars + failure panels."""

    name = "rich"

    LABEL_WIDTH = 30      # longest task label before it is ellipsised
    FRAME_WIDTH = 26      # icon + bar + elapsed + padding, i.e. non-detail space

    def __init__(self):
        from rich.console import Console
        from rich.panel import Panel
        from rich.progress import (BarColumn, Progress, TextColumn,
                                   TimeElapsedColumn)
        from rich.text import Text

        self._Panel = Panel
        self._Text = Text

        # Size the detail column to whatever is left of the terminal, so the
        # table never overflows and forces rich to collapse columns.
        cols = shutil.get_terminal_size((100, 24)).columns
        self.detail_width = max(16, min(70, cols - self.LABEL_WIDTH - self.FRAME_WIDTH))

        self.console = Console(file=sys.stdout, highlight=False, soft_wrap=False,
                               force_terminal=sys.stdout.isatty())
        self.progress = Progress(
            _make_state_column(),
            TextColumn("{task.fields[label]}"),
            TextColumn("[dim]{task.fields[detail]}[/dim]"),
            BarColumn(bar_width=10, complete_style="green",
                      finished_style="green", pulse_style="cyan"),
            TimeElapsedColumn(),
            console=self.console,
        )
        self._ids = {}
        self._tails = {}
        self._details = {}
        self._active = set()
        self.progress.start()

    # -- helpers -------------------------------------------------------------
    def _task_for(self, tid):
        task_id = self._ids.get(tid)
        return next((task for task in self.progress.tasks if task.id == task_id), None)

    def _set(self, tid, state, detail=""):
        task_id = self._ids.get(tid)
        if task_id is None:
            return
        task = self._task_for(tid)
        if task is None:
            return
        if detail:
            self._details[tid] = _shorten_to(detail, self.detail_width)
        finished = state in (STATE_DONE, STATE_FAILED, STATE_SKIPPED)
        if finished:
            self._active.discard(tid)
        else:
            self._active.add(tid)
            if task.total is not None:
                task.total = None
                task.completed = 0
                task.finished_time = None
        self.progress.update(task_id, state=state,
                             detail=self._details.get(tid, ""),
                             total=1 if finished else None,
                             completed=1 if finished else 0)

    # -- task lifecycle ------------------------------------------------------
    def task_add(self, tid, label):
        self._ids[tid] = self.progress.add_task(
            label, total=1, label=_head_to(label, self.LABEL_WIDTH),
            detail="", state=STATE_PENDING)
        self._tails.setdefault(tid, [])
        self._details.setdefault(tid, "")

    def task_start(self, tid, detail=""):
        self._set(tid, STATE_RUNNING, detail)

    def task_log(self, tid, line):
        tail = self._tails.setdefault(tid, [])
        tail.extend(trim_output(line, 1))
        del tail[:-MAX_TAIL]
        task = self._task_for(tid)
        if task and task.finished_time is None:
            self._set(tid, STATE_RUNNING, line)

    def _finish(self, tid, state, detail=""):
        self._set(tid, state, detail)
        if state == STATE_FAILED:
            tail = self._tails.get(tid, [])
            if tail:
                body = self._Text("\n".join(ln.rstrip() for ln in tail),
                                  style="dim red")
                self.console.print(self._Panel(body, title="output",
                                               border_style="red", padding=(0, 1)))

    def task_done(self, tid, detail=""):
        self._finish(tid, STATE_DONE, detail)

    def task_fail(self, tid, detail=""):
        self._finish(tid, STATE_FAILED, detail)

    def task_skip(self, tid, detail=""):
        self._finish(tid, STATE_SKIPPED, detail)

    # -- misc ----------------------------------------------------------------
    NOTE_STYLE = {"warn": "yellow", "err": "bold red", "ok": "green",
                  "step": "bold cyan", "info": None}

    def note(self, level, msg):
        style = self.NOTE_STYLE.get(level)
        tag = {"warn": "warn ", "err": "error", "step": "", "ok": "ok   "}.get(level, "info ")
        text = "%s %s" % (tag, msg)
        if style:
            self.console.print("[%s]%s[/]" % (style, text))
        else:
            self.console.print(text)

    def close(self):
        self.progress.stop()
        sys.stdout.flush()

    def tick(self):
        self.progress.refresh()


def make_backend():
    """Pick the best available renderer."""
    if os.environ.get("PDFTOOLS_PLAIN"):
        return PlainBackend()
    try:
        return RichBackend()
    except Exception:
        pass
    try:
        return TqdmBackend()
    except Exception:
        pass
    return PlainBackend()


# ---------------------------------------------------------------------------
# Event loop
# ---------------------------------------------------------------------------
def _handle(backend, ev):
    """Dispatch a single event. Rendering glitches must never abort setup."""
    if not isinstance(ev, dict):
        return True
    op = ev.get("op")
    tid = ev.get("id")
    try:
        if op == "task_add":
            backend.task_add(tid, ev.get("label", tid))
        elif op == "task_start":
            backend.task_start(tid, ev.get("detail", ""))
        elif op == "task_log":
            backend.task_log(tid, ev.get("line", ""))
        elif op == "task_done":
            backend.task_done(tid, ev.get("detail", ""))
        elif op == "task_fail":
            backend.task_fail(tid, ev.get("detail", ""))
        elif op == "task_skip":
            backend.task_skip(tid, ev.get("detail", ""))
        elif op == "note":
            backend.note(ev.get("level", "info"), ev.get("msg", ""))
        elif op == "done":
            return False
    except Exception:
        return True
    return True


def _reader(queue_):
    """Read stdin on a worker thread so events can be drained in batches."""
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            queue_.put(json.loads(raw))
        except Exception:
            continue
    queue_.put(None)          # EOF sentinel


def main():
    try:
        backend = make_backend()
    except Exception:
        backend = PlainBackend()

    # Events arrive in bursts (e.g. eight task_add calls back to back).
    # Batching them into one dispatch keeps the live table from redrawing
    # itself once per event.
    queue_ = queue.Queue()
    threading.Thread(target=_reader, args=(queue_,), daemon=True).start()

    alive = True
    while alive:
        batch = []
        try:
            batch.append(queue_.get(timeout=0.1))
        except queue.Empty:
            try:
                backend.tick()
            except Exception:
                pass
            continue
        while True:
            try:
                batch.append(queue_.get_nowait())
            except queue.Empty:
                break
        for ev in batch:
            if ev is None:            # EOF
                alive = False
                break
            alive = _handle(backend, ev) and alive

    try:
        backend.close()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (BrokenPipeError, KeyboardInterrupt):
        sys.exit(0)