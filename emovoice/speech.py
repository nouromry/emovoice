"""Input/output channels. The Assistant only ever talks to an IO object.

    IO.say(text)              speak / print a reply
    IO.listen(timeout=None)   next Utterance, or None when the channel is closed
    IO.event(kind, **data)    UI signals: mood, status, focus, reminder, image, help ...

TextIO is a terminal session; VoiceIO combines the microphone, a single
text-to-speech worker thread (pyttsx3 is not thread-safe) and a queue of typed
messages from the GUI.
"""

from __future__ import annotations

import logging
import queue
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable

from .config import Settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Utterance:
    text: str
    typed: bool = False   # typed input skips the wake word


class IO:
    shows_images = False

    def say(self, text: str) -> None:
        raise NotImplementedError

    def listen(self, timeout: float | None = None) -> Utterance | None:
        raise NotImplementedError

    def event(self, kind: str, **data) -> None:
        pass

    def open(self) -> None:
        """Called when a session starts."""

    def close(self) -> None:
        """Ask a blocked listen() to return None as soon as possible."""


class TextIO(IO):
    def __init__(self, input_fn: Callable[[str], str] = input, print_fn: Callable[[str], None] = print,
                 echo: bool | None = None):
        self._input = input_fn
        self._print = print_fn
        self._lock = threading.Lock()
        # When stdin is piped, echo what was read so transcripts make sense.
        self._echo = (not sys.stdin.isatty()) if echo is None and sys.stdin else bool(echo)

    def say(self, text: str) -> None:
        with self._lock:
            self._print(f"EmoVoice: {text}")

    def listen(self, timeout: float | None = None) -> Utterance | None:
        try:
            line = self._input("You: ")
        except (EOFError, KeyboardInterrupt):
            return None
        if self._echo:
            with self._lock:
                self._print(line)
        return Utterance(line, typed=True)

    def event(self, kind: str, **data) -> None:
        with self._lock:
            if kind == "image":
                self._print(f"  [{data.get('title', 'image')} saved to {data['path']}]")
            elif kind == "help":
                self._print(data["text"])
            elif kind == "focus" and data.get("done"):
                self._print("  [focus session complete]")


class _TTSWorker:
    """Owns the pyttsx3 engine on one thread; say() can be called from anywhere."""

    def __init__(self, rate: int, engine_factory: Callable | None = None):
        self.rate = rate
        self._factory = engine_factory
        self._queue: queue.Queue = queue.Queue()
        self.available = True
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="emovoice-tts", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=10)

    def _make_engine(self):
        if self._factory:
            return self._factory()
        try:  # SAPI needs COM initialised on the thread that uses it
            import pythoncom
            pythoncom.CoInitialize()
        except Exception:
            pass
        import pyttsx3
        return pyttsx3.init()

    def _run(self) -> None:
        try:
            engine = self._make_engine()
            engine.setProperty("rate", self.rate)
        except Exception as exc:
            log.warning("Text-to-speech unavailable: %s", exc)
            self.available = False
            engine = None
        self._ready.set()
        while True:
            item = self._queue.get()
            if item is None:
                break
            text, done = item
            try:
                if engine is not None:
                    engine.say(text)
                    engine.runAndWait()
            except Exception as exc:
                log.warning("Text-to-speech failed: %s", exc)
            finally:
                done.set()

    def say(self, text: str, wait: bool = True) -> None:
        done = threading.Event()
        self._queue.put((text, done))
        if wait:
            done.wait(timeout=5 + len(text) / 8)

    def shutdown(self) -> None:
        self._queue.put(None)


class VoiceIO(IO):
    """Microphone + speech output + typed messages, reporting everything via on_event."""

    def __init__(self, settings: Settings, on_event: Callable[[tuple[str, dict]], None] | None = None,
                 use_mic: bool = True, use_tts: bool = True, shows_images: bool = False,
                 engine_factory: Callable | None = None):
        self.settings = settings
        self._on_event = on_event or (lambda item: None)
        self.shows_images = shows_images
        self._typed: queue.Queue[str] = queue.Queue()
        self._closed = threading.Event()
        self._tts = _TTSWorker(settings.speech_rate, engine_factory) if use_tts else None
        self._want_mic = use_mic
        self._mic_ready = False
        self._recognizer = None
        self._microphone = None
        self._sr = None
        self._last_network_warning = 0.0

    # ------------------------------------------------------------------ output
    def say(self, text: str) -> None:
        self.event("say", text=text)
        if self._tts is not None:
            self._tts.say(text)

    def event(self, kind: str, **data) -> None:
        try:
            self._on_event((kind, data))
        except Exception:
            log.exception("Event handler failed")

    # ------------------------------------------------------------------ input
    def submit(self, text: str) -> None:
        """Typed text from the GUI (thread-safe)."""
        if text.strip():
            self._typed.put(text.strip())

    def open(self) -> None:
        self._closed.clear()
        if self._want_mic and not self._mic_ready:
            self._init_mic()

    def close(self) -> None:
        self._closed.set()

    @property
    def mic_available(self) -> bool:
        return self._mic_ready

    def _init_mic(self) -> None:
        try:
            import speech_recognition as sr
            self._sr = sr
            self._recognizer = sr.Recognizer()
            self._recognizer.dynamic_energy_threshold = True
            self._microphone = sr.Microphone()
            with self._microphone as source:  # calibrate once, not on every command
                self._recognizer.adjust_for_ambient_noise(source, duration=0.8)
            self._mic_ready = True
        except Exception as exc:
            log.warning("Microphone unavailable: %s", exc)
            self._mic_ready = False
            self.event("status", state="no-mic", text="No microphone found. Type your messages instead.")

    def listen(self, timeout: float | None = None) -> Utterance | None:
        deadline = time.monotonic() + timeout if timeout else None
        while not self._closed.is_set():
            try:
                return Utterance(self._typed.get_nowait(), typed=True)
            except queue.Empty:
                pass
            if deadline and time.monotonic() > deadline:
                return Utterance("", typed=False)
            if not self._mic_ready:
                try:
                    return Utterance(self._typed.get(timeout=0.2), typed=True)
                except queue.Empty:
                    continue
            heard = self._listen_mic()
            if heard is not None:
                return Utterance(heard, typed=False)
        return None

    def _listen_mic(self) -> str | None:
        """One short listening window. None = nothing heard; '' = speech not understood."""
        sr = self._sr
        try:
            with self._microphone as source:
                audio = self._recognizer.listen(source, timeout=self.settings.listen_timeout,
                                                phrase_time_limit=self.settings.phrase_time_limit)
        except sr.WaitTimeoutError:
            return None
        except Exception as exc:
            log.warning("Microphone error: %s", exc)
            self._mic_ready = False
            self.event("status", state="no-mic", text="Microphone stopped working. Type your messages instead.")
            return None
        try:
            text = self._recognizer.recognize_google(audio, language=self.settings.language)
            return text.lower()
        except sr.UnknownValueError:
            return ""
        except sr.RequestError as exc:
            log.warning("Speech service error: %s", exc)
            if time.monotonic() - self._last_network_warning > 120:
                self._last_network_warning = time.monotonic()
                self.event("status", state="offline",
                           text="I can't reach the speech service right now. You can type instead.")
            return None
