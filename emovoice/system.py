"""Cross-platform side effects: opening files/apps/URLs and non-blocking music.

Everything here is wrapped by small classes so tests can swap in fakes.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
import warnings
import webbrowser
from pathlib import Path
from typing import Iterable
from urllib.parse import quote_plus

log = logging.getLogger(__name__)

YOUTUBE_URL = "https://www.youtube.com"
_SKIP_DIRS = {"venv", ".venv", "node_modules", "__pycache__", "site-packages", "AppData", "Library"}


class System:
    def __init__(self, platform: str | None = None):
        self.platform = platform or sys.platform

    @property
    def is_windows(self) -> bool:
        return self.platform.startswith("win")

    @property
    def is_mac(self) -> bool:
        return self.platform == "darwin"

    def _spawn(self, args: list[str]) -> bool:
        try:
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except OSError as exc:
            log.warning("Could not run %s: %s", args[0], exc)
            return False

    def open_path(self, path: Path | str) -> bool:
        path = str(path)
        if self.is_windows:
            try:
                os.startfile(path)  # type: ignore[attr-defined]
                return True
            except OSError as exc:
                log.warning("Could not open %s: %s", path, exc)
                return False
        if self.is_mac:
            return self._spawn(["open", path])
        if shutil.which("xdg-open"):
            return self._spawn(["xdg-open", path])
        return False

    def open_url(self, url: str) -> bool:
        try:
            return bool(webbrowser.open(url))
        except Exception as exc:
            log.warning("Could not open %s: %s", url, exc)
            return False

    def open_word(self) -> bool:
        """Microsoft Word where available; LibreOffice Writer (or similar) otherwise."""
        if self.is_windows:
            try:
                os.startfile("winword")  # type: ignore[attr-defined]
                return True
            except OSError:
                pass
            for exe in ("soffice", "swriter"):
                if shutil.which(exe):
                    return self._spawn([exe, "--writer"])
            return False
        if self.is_mac:
            for app in ("Microsoft Word", "LibreOffice", "Pages", "TextEdit"):
                try:
                    if subprocess.run(["open", "-a", app], capture_output=True).returncode == 0:
                        return True
                except OSError:
                    return False
            return False
        for cmd in (["libreoffice", "--writer"], ["soffice", "--writer"], ["lowriter"], ["abiword"]):
            if shutil.which(cmd[0]):
                return self._spawn(cmd)
        return False

    def play_youtube(self, query: str) -> bool:
        try:
            import pywhatkit  # imported lazily: it needs a network connection on import
            pywhatkit.playonyt(query)
            return True
        except Exception as exc:
            log.info("pywhatkit unavailable (%s); falling back to a YouTube search page", exc)
            return self.open_url(f"{YOUTUBE_URL}/results?search_query={quote_plus(query)}")

    def find_file(self, name: str, search_dirs: Iterable[Path], max_depth: int = 2) -> Path | None:
        """Find a file by spoken name ("budget dot xlsx", "notes") in a few likely folders."""
        wanted = " ".join(name.lower().replace(" dot ", ".").split()).strip("'\" ")
        if not wanted:
            return None
        direct = Path(os.path.expanduser(wanted))
        if direct.is_absolute() and direct.is_file():
            return direct
        stem_match = None
        for root_dir in search_dirs:
            root_dir = Path(root_dir)
            base_depth = len(root_dir.parts)
            for dirpath, dirnames, filenames in os.walk(root_dir):
                if len(Path(dirpath).parts) - base_depth >= max_depth:
                    dirnames[:] = []
                dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in _SKIP_DIRS]
                for filename in filenames:
                    lower = filename.lower()
                    if lower == wanted:
                        return Path(dirpath) / filename
                    if stem_match is None and (Path(lower).stem == wanted or lower.replace("_", " ") == wanted):
                        stem_match = Path(dirpath) / filename
        return stem_match


class MusicPlayer:
    """Plays audio without blocking. Tries pygame, then a command-line player, then playsound."""

    def __init__(self):
        self._lock = threading.Lock()
        self.backend: str | None = None
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._pygame = None

    def play(self, path: Path | str) -> str:
        self.stop()
        path = str(path)
        with self._lock:
            if self._play_pygame(path):
                self.backend = "pygame"
            elif self._play_process(path):
                self.backend = "process"
            else:
                self._play_playsound(path)
                self.backend = "playsound"
            return self.backend

    def _play_pygame(self, path: str) -> bool:
        try:
            if self._pygame is None:
                os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    import pygame
                self._pygame = pygame
            mixer = self._pygame.mixer
            if not mixer.get_init():
                mixer.init()
            mixer.music.load(path)
            mixer.music.play()
            return True
        except Exception as exc:
            log.info("pygame playback unavailable: %s", exc)
            return False

    def _play_process(self, path: str) -> bool:
        players = [["afplay", path]] if sys.platform == "darwin" else []
        players += [["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path],
                    ["mpg123", "-q", path], ["cvlc", "--play-and-exit", "--quiet", path]]
        for cmd in players:
            if shutil.which(cmd[0]):
                try:
                    self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return True
                except OSError:
                    continue
        return False

    def _play_playsound(self, path: str) -> None:
        def run():
            try:
                from playsound import playsound
                playsound(path)
            except Exception as exc:
                log.warning("playsound failed: %s", exc)
        self._thread = threading.Thread(target=run, name="emovoice-music", daemon=True)
        self._thread.start()

    @property
    def can_stop(self) -> bool:
        return self.backend in ("pygame", "process", None)

    @property
    def is_playing(self) -> bool:
        if self.backend == "pygame" and self._pygame is not None:
            try:
                return bool(self._pygame.mixer.get_init() and self._pygame.mixer.music.get_busy())
            except Exception:
                return False
        if self.backend == "process" and self._proc is not None:
            return self._proc.poll() is None
        if self.backend == "playsound" and self._thread is not None:
            return self._thread.is_alive()
        return False

    def stop(self) -> bool:
        """Stop playback. Returns True if something was stopped."""
        was_playing = self.is_playing
        if self.backend == "pygame" and self._pygame is not None:
            try:
                self._pygame.mixer.music.stop()
            except Exception:
                pass
        elif self.backend == "process" and self._proc is not None:
            self._proc.terminate()
            self._proc = None
        elif self.backend == "playsound":
            return False  # playsound cannot be interrupted; the daemon thread ends with the app
        return was_playing
