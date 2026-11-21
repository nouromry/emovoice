"""Thread-safe persistence for moods, notes, to-dos, reminders and gratitude.

All JSON is written atomically (temp file + os.replace). A file that cannot be
parsed is renamed to *.corrupt and treated as empty instead of crashing the app.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

MOODS = ("happy", "neutral", "anxious", "sad", "angry")
LOW_MOODS = frozenset({"sad", "angry", "anxious"})
_MOOD_ALIASES = {
    "joy": "happy", "joyful": "happy", "glad": "happy", "positive": "happy",
    "calm": "neutral", "ok": "neutral", "okay": "neutral",
    "stressed": "anxious", "stress": "anxious", "fear": "anxious", "worried": "anxious", "anxiety": "anxious",
    "anger": "angry", "mad": "angry", "upset": "sad", "negative": "sad", "unhappy": "sad",
}
_TIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d")
STAMP = "%Y-%m-%d %H:%M"


def atomic_write_text(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:  # Windows: target briefly held open by a reader
                if attempt == 4:
                    raise
                time.sleep(0.05)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def quarantine(path: Path) -> Path:
    """Rename an unreadable file to <name>.corrupt (adding a timestamp if one already exists)."""
    path = Path(path)
    target = path.with_name(path.name + ".corrupt")
    if target.exists():
        target = path.with_name(f"{path.name}.{datetime.now():%Y%m%d%H%M%S}.corrupt")
    os.replace(path, target)
    log.warning("%s was corrupt and has been moved to %s", path, target)
    return target


def parse_timestamp(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    value = value.strip()
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(value).replace(tzinfo=None)
    except ValueError:
        return None


def normalize_mood(value) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    value = _MOOD_ALIASES.get(value, value)
    return value if value in MOODS else None


@dataclass(frozen=True)
class MoodEntry:
    when: datetime | None
    mood: str
    score: float | None = None

    def to_json(self) -> dict:
        data = {"timestamp": self.when.strftime(STAMP) if self.when else None, "mood": self.mood}
        if self.score is not None:
            data["score"] = round(self.score, 3)
        return data


def mood_entry_from_json(raw) -> MoodEntry | None:
    """Accept the v1 format, plain strings, and loosely shaped dicts."""
    if isinstance(raw, str):
        mood = normalize_mood(raw)
        return MoodEntry(None, mood) if mood else None
    if not isinstance(raw, dict):
        return None
    mood = normalize_mood(raw.get("mood", raw.get("emotion", raw.get("feeling"))))
    if not mood:
        return None
    when = None
    for key in ("timestamp", "time", "date", "when", "datetime"):
        if key in raw:
            when = parse_timestamp(raw[key])
            if when:
                break
    score = raw.get("score")
    score = float(score) if isinstance(score, (int, float)) and not isinstance(score, bool) else None
    return MoodEntry(when, mood, score)


@dataclass(frozen=True)
class Reminder:
    id: str
    message: str
    due: datetime
    created: datetime

    def to_json(self) -> dict:
        return {"id": self.id, "message": self.message,
                "due": self.due.isoformat(timespec="seconds"),
                "created": self.created.isoformat(timespec="seconds")}

    @classmethod
    def from_json(cls, raw) -> "Reminder | None":
        if not isinstance(raw, dict):
            return None
        due = parse_timestamp(raw.get("due"))
        if not due:
            return None
        created = parse_timestamp(raw.get("created")) or due
        return cls(str(raw.get("id") or uuid.uuid4().hex[:8]), str(raw.get("message") or ""), due, created)


@dataclass(frozen=True)
class GratitudeEntry:
    when: datetime | None
    text: str


class Store:
    def __init__(self, data_dir: Path | str, history_limit: int = 1000):
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.history_limit = history_limit
        self._lock = threading.RLock()
        self.moods_file = self.data_dir / "mood_history.json"
        self.notes_file = self.data_dir / "notes.txt"
        self.todo_file = self.data_dir / "todo.txt"
        self.reminders_file = self.data_dir / "reminders.json"
        self.gratitude_file = self.data_dir / "gratitude.json"

    # ------------------------------------------------------------------ helpers
    def _read_json(self, path: Path, default):
        if not path.exists():
            return default
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            log.warning("Could not read %s: %s", path, exc)
            return default
        if not text.strip():
            return default
        try:
            data = json.loads(text)
        except ValueError:
            quarantine(path)
            return default
        if not isinstance(data, type(default)):
            quarantine(path)
            return default
        return data

    def _write_json(self, path: Path, data) -> None:
        atomic_write_text(path, json.dumps(data, indent=1, ensure_ascii=False))

    def _read_lines(self, path: Path) -> list[str]:
        if not path.exists():
            return []
        text = path.read_text(encoding="utf-8", errors="replace")
        return [line.strip() for line in text.splitlines() if line.strip()]

    def _write_lines(self, path: Path, lines: list[str]) -> None:
        atomic_write_text(path, "".join(line + "\n" for line in lines))

    # ------------------------------------------------------------------ moods
    def moods(self) -> list[MoodEntry]:
        with self._lock:
            raw = self._read_json(self.moods_file, [])
        return [e for e in (mood_entry_from_json(r) for r in raw) if e]

    def add_mood(self, mood: str, when: datetime, score: float | None = None) -> MoodEntry:
        mood = normalize_mood(mood) or "neutral"
        entry = MoodEntry(when, mood, score)
        with self._lock:
            entries = [e.to_json() for e in self.moods()] + [entry.to_json()]
            self._write_json(self.moods_file, entries[-self.history_limit:])
        return entry

    # ------------------------------------------------------------------ notes
    def notes(self) -> list[str]:
        with self._lock:
            return self._read_lines(self.notes_file)

    def add_note(self, text: str, when: datetime) -> str:
        line = f"{when.strftime(STAMP)}  {' '.join(text.split())}"
        with self._lock:
            self._write_lines(self.notes_file, self.notes() + [line])
        return line

    # ------------------------------------------------------------------ to-dos
    def todos(self) -> list[str]:
        with self._lock:
            return self._read_lines(self.todo_file)

    def add_todo(self, item: str) -> str:
        item = " ".join(item.split())
        with self._lock:
            self._write_lines(self.todo_file, self.todos() + [item])
        return item

    def remove_todo(self, query: str | int) -> tuple[str | None, list[str]]:
        """Remove by 1-based index (negative counts from the end) or by name.

        Returns (removed_item, candidates). When the name is ambiguous nothing is
        removed and candidates lists the matching items.
        """
        with self._lock:
            items = self.todos()
            index = None
            if isinstance(query, int):
                index = query - 1 if query > 0 else len(items) + query
                if not 0 <= index < len(items):
                    return None, []
            else:
                q = " ".join(str(query).lower().split())
                if not q:
                    return None, []
                exact = [i for i, item in enumerate(items) if item.lower() == q]
                partial = [i for i, item in enumerate(items) if q in item.lower()]
                if exact:
                    index = exact[0]
                elif len(partial) == 1:
                    index = partial[0]
                else:
                    return None, [items[i] for i in partial]
            removed = items.pop(index)
            self._write_lines(self.todo_file, items)
            return removed, [removed]

    def clear_todos(self) -> int:
        with self._lock:
            count = len(self.todos())
            self._write_lines(self.todo_file, [])
            return count

    # ------------------------------------------------------------------ reminders
    def reminders(self) -> list[Reminder]:
        with self._lock:
            raw = self._read_json(self.reminders_file, [])
        items = [r for r in (Reminder.from_json(x) for x in raw) if r]
        return sorted(items, key=lambda r: r.due)

    def add_reminder(self, message: str, due: datetime, now: datetime) -> Reminder:
        reminder = Reminder(uuid.uuid4().hex[:8], message, due, now)
        with self._lock:
            self._save_reminders(self.reminders() + [reminder])
        return reminder

    def pop_due(self, now: datetime) -> list[Reminder]:
        """Remove and return every reminder whose time has come."""
        with self._lock:
            items = self.reminders()
            due = [r for r in items if r.due <= now]
            if due:
                self._save_reminders([r for r in items if r.due > now])
            return due

    def clear_reminders(self) -> int:
        with self._lock:
            count = len(self.reminders())
            self._save_reminders([])
            return count

    def _save_reminders(self, items: list[Reminder]) -> None:
        self._write_json(self.reminders_file, [r.to_json() for r in sorted(items, key=lambda r: r.due)])

    # ------------------------------------------------------------------ gratitude
    def gratitude(self) -> list[GratitudeEntry]:
        with self._lock:
            raw = self._read_json(self.gratitude_file, [])
        out = []
        for r in raw:
            if isinstance(r, dict) and str(r.get("text", "")).strip():
                out.append(GratitudeEntry(parse_timestamp(r.get("timestamp")), str(r["text"]).strip()))
            elif isinstance(r, str) and r.strip():
                out.append(GratitudeEntry(None, r.strip()))
        return out

    def add_gratitude(self, text: str, when: datetime) -> GratitudeEntry:
        entry = GratitudeEntry(when, " ".join(text.split()))
        with self._lock:
            raw = [{"timestamp": e.when.strftime(STAMP) if e.when else None, "text": e.text}
                   for e in self.gratitude()]
            raw.append({"timestamp": when.strftime(STAMP), "text": entry.text})
            self._write_json(self.gratitude_file, raw)
        return entry
