"""Background timers: persistent reminders and the focus (Pomodoro) countdown.

Both expose a synchronous method (check_now / tick) so tests can drive them
with a fake clock instead of waiting on real threads.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Callable

from .storage import Reminder, Store

log = logging.getLogger(__name__)


class ReminderScheduler:
    def __init__(self, store: Store, on_due: Callable[[Reminder], None],
                 clock: Callable[[], datetime] = datetime.now, interval: float = 1.0):
        self.store = store
        self.on_due = on_due
        self.clock = clock
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def collect_missed(self, now: datetime | None = None) -> list[Reminder]:
        """Reminders that fell due while the app was closed. Call before start()."""
        return self.store.pop_due(now or self.clock())

    def check_now(self, now: datetime | None = None) -> list[Reminder]:
        due = self.store.pop_due(now or self.clock())
        for reminder in due:
            try:
                self.on_due(reminder)
            except Exception:
                log.exception("Reminder callback failed")
        return due

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="emovoice-reminders", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.check_now()
            except Exception:
                log.exception("Reminder check failed")


class FocusTimer:
    """A countdown that reports every tick and calls on_done once when it reaches zero."""

    def __init__(self, on_tick: Callable[[int, int], None], on_done: Callable[[int], None],
                 clock: Callable[[], float] = time.monotonic, interval: float = 1.0):
        self.on_tick = on_tick
        self.on_done = on_done
        self.clock = clock
        self.interval = interval
        self._lock = threading.Lock()
        self._end: float | None = None
        self._start: float | None = None
        self.total = 0
        self._stop_event: threading.Event | None = None

    @property
    def running(self) -> bool:
        return self._end is not None

    def remaining(self, now: float | None = None) -> int:
        if self._end is None:
            return 0
        return max(0, round(self._end - (self.clock() if now is None else now)))

    def start(self, minutes: float, threaded: bool = True) -> None:
        self.stop()
        with self._lock:
            self._start = self.clock()
            self.total = max(1, round(minutes * 60))
            self._end = self._start + self.total
            stop_event = self._stop_event = threading.Event()
        self.on_tick(self.total, self.total)
        if threaded:
            threading.Thread(target=self._run, args=(stop_event,), name="emovoice-focus", daemon=True).start()

    def stop(self) -> int | None:
        """Cancel the session; return the seconds focused so far (None if nothing was running)."""
        with self._lock:
            if self._end is None:
                return None
            elapsed = round(self.clock() - self._start)
            self._end = self._start = None
            if self._stop_event:
                self._stop_event.set()
        self.on_tick(0, self.total)
        return elapsed

    def tick(self, now: float | None = None) -> int:
        with self._lock:
            if self._end is None:
                return 0
            remaining = self.remaining(now)
            finished = remaining <= 0
            if finished:
                self._end = self._start = None
                if self._stop_event:
                    self._stop_event.set()
        self.on_tick(remaining, self.total)
        if finished:
            self.on_done(self.total)
        return remaining

    def _run(self, stop_event: threading.Event) -> None:
        while not stop_event.wait(self.interval):
            try:
                self.tick()
            except Exception:
                log.exception("Focus timer tick failed")
