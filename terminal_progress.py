"""Small cross-platform terminal progress renderer shared by the CLI tools."""

from __future__ import annotations

import math
import shutil
import sys
import threading
import time
from contextlib import contextmanager


class DynamicProgressBar:
    def __init__(self, total: int, title: str = "Processing", width: int = 24,
                 use_color: bool = True, unit: str = "items"):
        self.total = max(1, total)
        self.title = title
        self.width = max(5, width)
        self.unit = unit
        self.is_tty = sys.stdout.isatty()
        self.current = 0.0
        self.item_name = ""
        self.stage = "PROCESSING"
        self.started = time.monotonic()
        self._last_render = 0.0
        self._rendered_state = None
        self._last_line_length = 0
        self._last_milestone = -1
        self._lock = threading.Lock()

    @staticmethod
    def _format_time(seconds: float) -> str:
        seconds = max(0, int(seconds))
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"

    def update(self, current: float, item_name: str = "", stage: str = "PROCESSING",
               status_tag: str | None = None) -> None:
        with self._lock:
            self.current = min(float(self.total), max(self.current, float(current), 0.0))
            if item_name:
                self.item_name = item_name
            self.stage = status_tag or stage
            self._render()

    def _render(self, force: bool = False) -> None:
        now = time.monotonic()
        state = (self.current, self.stage, self.item_name)
        if (self.is_tty and not force and state == self._rendered_state
                and now - self._last_render < 0.08):
            return
        self._last_render = now
        self._rendered_state = state

        fraction = min(1.0, self.current / self.total)
        percent = fraction * 100
        elapsed = now - self.started
        rate = self.current / elapsed if elapsed > 0.05 else 0.0
        eta = (self.total - self.current) / rate if rate else 0.0
        filled = int(self.width * fraction)
        spinner = "|/-\\"[int(now * 10) % 4]
        current_text = f"{self.current:.1f}"
        if current_text.endswith(".0"):
            current_text = current_text[:-2]
        term_width = shutil.get_terminal_size((100, 24)).columns
        name_width = max(8, min(48, term_width - self.width - 50))
        name = self.item_name
        if len(name) > name_width:
            name = "..." + name[-(name_width - 3):]
        line = (
            f"{self.title} [{ '#' * filled}{'-' * (self.width - filled)}] "
            f"{percent:5.1f}% ({current_text}/{self.total}) {spinner} "
            f"{self.stage} {name} [{self._format_time(elapsed)} < {self._format_time(eta)}]"
        )
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        line = line.encode(encoding, errors="replace").decode(encoding, errors="replace")

        if self.is_tty:
            padded = line.ljust(self._last_line_length)
            sys.stdout.write("\r" + padded)
            self._last_line_length = len(line)
            sys.stdout.flush()
            return

        milestone = math.floor(percent / 10)
        if force or milestone > self._last_milestone:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
            self._last_milestone = milestone

    @contextmanager
    def activity(self, current: float, item_name: str, stage: str):
        """Animate an in-progress operation whose exact completion is unknown."""
        self.update(current, item_name, stage)
        stopped = threading.Event()

        def pulse():
            while self.is_tty and not stopped.wait(0.1):
                with self._lock:
                    self._render(force=True)

        worker = threading.Thread(target=pulse, daemon=True)
        worker.start()
        try:
            yield
        finally:
            stopped.set()
            worker.join()

    def complete(self, message: str = "Complete") -> None:
        with self._lock:
            self.current = float(self.total)
            self.stage = message
            self._render(force=True)
            if self.is_tty:
                sys.stdout.write("\n")
                sys.stdout.flush()
