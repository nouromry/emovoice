"""Absolute paths, user settings, asset lookup and legacy-data migration.

Every path here is derived from the package location, so the app behaves the
same no matter which working directory it is started from.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

log = logging.getLogger(__name__)

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent
ASSETS_DIR = PROJECT_DIR / "assets"
DATA_DIR = PROJECT_DIR / "data"
SETTINGS_FILE = PROJECT_DIR / "settings.json"


@dataclass
class Settings:
    user_name: str = ""
    wake_words: list[str] = field(default_factory=lambda: ["emo"])
    stop_words: list[str] = field(
        default_factory=lambda: ["stop", "exit", "quit", "goodbye", "bye", "stop listening"]
    )
    language: str = "en-US"
    speech_rate: int = 160
    listen_timeout: float = 1.0       # seconds to wait for speech to start before re-checking typed input
    phrase_time_limit: float = 10.0   # longest single utterance, in seconds
    answer_timeout: float = 10.0      # how long a follow-up question waits for an answer
    music_file: str = "calm.mp3"
    focus_minutes: int = 25
    history_limit: int = 1000
    open_images: bool = True          # terminal modes open charts/quotes in the OS image viewer


def load_settings(path: Path | str | None = None) -> Settings:
    """Read settings.json if present. Unknown keys and wrongly typed values are ignored."""
    settings = Settings()
    path = Path(path) if path else SETTINGS_FILE
    if not path.exists():
        return settings
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("Ignoring unreadable settings file %s: %s", path, exc)
        return settings
    if not isinstance(raw, dict):
        log.warning("Ignoring settings file %s: expected a JSON object", path)
        return settings

    for f in dataclasses.fields(Settings):
        if f.name not in raw:
            continue
        value = _coerce(raw[f.name], getattr(settings, f.name))
        if value is None:
            log.warning("Ignoring setting %r: unexpected value %r", f.name, raw[f.name])
        else:
            setattr(settings, f.name, value)
    return settings


def _coerce(value, default):
    """Convert value to the type of default, or return None when that is not sensible."""
    if isinstance(default, bool):
        return value if isinstance(value, bool) else None
    if isinstance(default, (int, float)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            return None
        return type(default)(value)
    if isinstance(default, list):
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return None
        words = [str(v).strip().lower() for v in value if str(v).strip()]
        return words or None
    if isinstance(default, str):
        return value.strip() if isinstance(value, str) else None
    return None


def find_asset(name: str, assets_dir: Path = ASSETS_DIR, project_dir: Path = PROJECT_DIR) -> Path | None:
    """Locate a media file: an absolute path, then assets/, assets/*/, then the project root."""
    if not name:
        return None
    candidate = Path(name)
    if candidate.is_absolute():
        return candidate if candidate.is_file() else None
    search = [assets_dir / name]
    if assets_dir.is_dir():
        search += [sub / name for sub in sorted(assets_dir.iterdir()) if sub.is_dir()]
    search.append(project_dir / name)
    for path in search:
        if path.is_file():
            return path
    return None


# --------------------------------------------------------------------------- migration

def migrate_legacy(project_dir: Path = PROJECT_DIR, data_dir: Path = DATA_DIR) -> list[str]:
    """Move v1 files from the project root into data/.

    The old root mood_history.json is merged into data/mood_history.json and the old
    notes.txt is appended to data/notes.txt. Originals are kept as *.migrated.
    Returns a list of human-readable actions for logging.
    """
    from .storage import atomic_write_text, quarantine  # local import avoids a cycle

    actions: list[str] = []
    project_dir, data_dir = Path(project_dir), Path(data_dir)
    if project_dir.resolve() == data_dir.resolve():
        return actions
    data_dir.mkdir(parents=True, exist_ok=True)

    old_moods = project_dir / "mood_history.json"
    if old_moods.is_file():
        new_moods = data_dir / "mood_history.json"
        legacy = _read_json_list(old_moods, quarantine)
        current = _read_json_list(new_moods, quarantine) if new_moods.exists() else []
        seen = {json.dumps(e, sort_keys=True) for e in current}
        merged = current + [e for e in legacy if json.dumps(e, sort_keys=True) not in seen]
        atomic_write_text(new_moods, json.dumps(merged, indent=1))
        _retire(old_moods)
        actions.append(f"merged {len(legacy)} mood entries into {new_moods}")

    old_notes = project_dir / "notes.txt"
    if old_notes.is_file():
        text = old_notes.read_text(encoding="utf-8", errors="replace")
        stamp = datetime.fromtimestamp(old_notes.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        lines = [f"{stamp}  {line.strip()}" for line in text.splitlines() if line.strip()]
        if lines:
            new_notes = data_dir / "notes.txt"
            existing = new_notes.read_text(encoding="utf-8", errors="replace") if new_notes.exists() else ""
            if existing and not existing.endswith("\n"):
                existing += "\n"
            atomic_write_text(new_notes, existing + "\n".join(lines) + "\n")
        _retire(old_notes)
        actions.append(f"moved {len(lines)} note(s) into data/notes.txt")

    for action in actions:
        log.info("Migration: %s", action)
    return actions


def _read_json_list(path: Path, quarantine) -> list:
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            return []
        data = json.loads(text)
    except ValueError:
        quarantine(path)
        return []
    return data if isinstance(data, list) else []


def _retire(path: Path) -> Path:
    target = path.with_name(path.name + ".migrated")
    n = 1
    while target.exists():
        n += 1
        target = path.with_name(f"{path.name}.migrated{n}")
    os.replace(path, target)
    return target


def search_dirs(data_dir: Path = DATA_DIR) -> Iterable[Path]:
    """Folders searched by "open file", most specific first."""
    home = Path.home()
    for d in (data_dir, PROJECT_DIR, home / "Documents", home / "Desktop", home / "Downloads", home):
        if d.is_dir():
            yield d
