"""EmoVoice test suite: no microphone, speakers, display or internet needed.

Sections follow the project brief: setup bugs, logic bugs, robustness, features.
"""

from __future__ import annotations

import json
import os
import queue
import random
import re
import subprocess
import sys
import threading
import time
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from emovoice import config, content, visuals
from emovoice.assistant import Assistant
from emovoice.config import Settings, find_asset, load_settings, migrate_legacy
from emovoice.intents import (INTENT_NAMES, contains_phrase, is_exact_command, parse_index, parse_intent,
                              split_wake_word)
from emovoice.mood import detect_mood
from emovoice.reminders import FocusTimer, ReminderScheduler
from emovoice.speech import IO, TextIO, Utterance, VoiceIO
from emovoice.storage import MoodEntry, Store
from emovoice.system import MusicPlayer, System
from emovoice.timeparse import format_clock, parse_minutes, parse_reminder, say_time

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 20, 14, 0)  # a Friday afternoon


# --------------------------------------------------------------------------- fakes

class FakeIO(IO):
    shows_images = True

    def __init__(self, inputs=()):
        self.inputs = list(inputs)
        self.said: list[str] = []
        self.events: list[tuple[str, dict]] = []
        self._lock = threading.Lock()

    def say(self, text):
        with self._lock:
            self.said.append(text)

    def listen(self, timeout=None):
        if not self.inputs:
            return None
        item = self.inputs.pop(0)
        return Utterance(*item) if isinstance(item, tuple) else Utterance(item, True)

    def event(self, kind, **data):
        with self._lock:
            self.events.append((kind, data))

    def kinds(self, kind):
        return [d for k, d in self.events if k == kind]

    def text(self):
        return "\n".join(self.said)


class FakeSystem:
    def __init__(self, file=None):
        self.file = file
        self.opened, self.urls, self.youtube, self.word = [], [], [], 0

    def open_path(self, path):
        self.opened.append(Path(path))
        return True

    def open_url(self, url):
        self.urls.append(url)
        return True

    def open_word(self):
        self.word += 1
        return True

    def play_youtube(self, query):
        self.youtube.append(query)
        return True

    def find_file(self, name, dirs):
        return self.file


class FakePlayer:
    def __init__(self):
        self.playing = None
        self.played = []

    def play(self, path):
        self.playing = path
        self.played.append(path)
        return "fake"

    @property
    def is_playing(self):
        return self.playing is not None

    def stop(self):
        was = self.playing is not None
        self.playing = None
        return was


def make(tmp_path, inputs=(), now=NOW, settings=None):
    io = FakeIO(inputs)
    store = Store(tmp_path / "data")
    sleeps: list[float] = []
    a = Assistant(io, store, settings or Settings(), system=FakeSystem(), player=FakePlayer(),
                  clock=lambda: now, sleep=sleeps.append, rng=random.Random(0))
    a.sleeps = sleeps
    return a, io, store


# =========================================================================== section 2: setup bugs

def _requirement_names(path):
    names = []
    for line in path.read_text().splitlines():
        line = line.split("#")[0].strip()
        if line:
            names.append(re.split(r"[<>=!~\[; ]", line)[0].lower())
    return names


def test_requirements_list_pillow_matplotlib_and_not_tk():
    names = _requirement_names(ROOT / "requirements.txt")
    assert "pillow" in names and "matplotlib" in names
    assert "pygame" in names
    assert "tk" not in names
    assert "pytest" in _requirement_names(ROOT / "requirements-dev.txt")


def test_find_asset_prefers_assets_then_falls_back_to_root(tmp_path):
    assets = tmp_path / "assets"
    (assets / "sounds").mkdir(parents=True)
    (assets / "a.mp3").write_text("a")
    (assets / "sounds" / "b.mp3").write_text("b")
    (tmp_path / "a.mp3").write_text("root a")
    (tmp_path / "c.mp3").write_text("c")
    assert find_asset("a.mp3", assets, tmp_path) == assets / "a.mp3"
    assert find_asset("b.mp3", assets, tmp_path) == assets / "sounds" / "b.mp3"
    assert find_asset("c.mp3", assets, tmp_path) == tmp_path / "c.mp3"
    assert find_asset("missing.mp3", assets, tmp_path) is None
    assert find_asset("calm.mp3") is not None  # the real project file still resolves


@pytest.mark.parametrize("platform, expected", [("linux", ["xdg-open"]), ("darwin", ["open"])])
def test_open_path_is_cross_platform(monkeypatch, platform, expected):
    calls = []
    monkeypatch.setattr("emovoice.system.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("emovoice.system.subprocess.Popen", lambda args, **kw: calls.append(args))
    assert System(platform).open_path("/tmp/x.png")
    assert calls == [expected + ["/tmp/x.png"]]


def test_open_path_on_windows_uses_startfile(monkeypatch):
    opened = []
    monkeypatch.setattr(os, "startfile", opened.append, raising=False)
    assert System("win32").open_path("C:/x.png")
    assert opened == ["C:/x.png"]


def test_open_word_uses_libreoffice_on_linux(monkeypatch):
    calls = []
    monkeypatch.setattr("emovoice.system.shutil.which", lambda name: "/usr/bin/x" if name == "libreoffice" else None)
    monkeypatch.setattr("emovoice.system.subprocess.Popen", lambda args, **kw: calls.append(args))
    assert System("linux").open_word()
    assert calls == [["libreoffice", "--writer"]]


def test_paths_are_absolute_and_package_based():
    for p in (config.PROJECT_DIR, config.DATA_DIR, config.ASSETS_DIR, config.SETTINGS_FILE):
        assert p.is_absolute()
    assert config.PROJECT_DIR == ROOT


@pytest.mark.parametrize("launcher", ["script", "module"])
def test_cli_text_mode_works_from_any_directory(tmp_path, launcher):
    cmd = [sys.executable, str(ROOT / "emovoice.py")] if launcher == "script" else [sys.executable, "-m", "emovoice"]
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8")
    result = subprocess.run(cmd + ["--text", "--name", "Sam", "--data-dir", str(tmp_path / "d")],
                            input="add milk to my list\nwhat's on my list\nstop\n", capture_output=True,
                            text=True, encoding="utf-8", cwd=tmp_path, env=env, timeout=60)
    assert result.returncode == 0, result.stderr
    assert "Sam" in result.stdout
    assert "1: milk" in result.stdout
    assert (tmp_path / "d" / "todo.txt").read_text().strip() == "milk"


# =========================================================================== section 2: logic bugs

def test_no_keyword_sentence_gets_exactly_one_response(tmp_path):
    a, io, store = make(tmp_path)
    a.handle("i had a lovely day at the park")
    assert len(io.said) == 1
    assert content.UNKNOWN not in io.said
    assert store.moods()[-1].mood == "happy"

    io.said.clear()
    a.handle("purple monkey dishwasher")
    assert io.said == [content.UNKNOWN]


@pytest.mark.parametrize("phrase, mood", [("i'm sad", "sad"), ("i am sad", "sad"), ("i'm angry", "angry"),
                                          ("i'm happy", "happy")])
def test_mood_statements_reach_the_mood_handler(tmp_path, phrase, mood):
    a, io, store = make(tmp_path)
    assert parse_intent(phrase) is None
    a.handle(phrase)
    assert store.moods()[-1].mood == mood
    assert io.kinds("mood")[-1]["mood"] == mood


@pytest.mark.parametrize("text, mood", [
    ("i am sad", "sad"),
    ("im feeling really low and lonely", "sad"),
    ("i am angry", "angry"),                      # no "!" needed
    ("i'm so angry right now", "angry"),
    ("i hate my job", "angry"),
    ("i am fed up with this", "angry"),
    ("i am not happy", "sad"),                    # negation
    ("i dont feel good", "sad"),                  # unpunctuated negation
    ("i am stressed about my exam", "anxious"),
    ("i'm worried about tomorrow", "anxious"),
    ("i am freaking out", "anxious"),
    ("i am really happy today", "happy"),
    ("i had a lovely day at the park", "happy"),
    ("what is the weather", "neutral"),
    ("i am fine", "neutral"),
])
def test_detect_mood(text, mood):
    assert detect_mood(text).mood == mood


def test_negated_sadness_is_not_sad_and_intensifiers_raise_confidence():
    assert detect_mood("i am not sad").mood != "sad"
    assert detect_mood("i am not angry anymore").mood != "angry"
    weak = detect_mood("i am a little sad", use_polarity=False)
    strong = detect_mood("i am very sad", use_polarity=False)
    assert weak.mood == strong.mood == "sad"
    assert strong.confidence > weak.confidence


def test_mood_result_fields():
    r = detect_mood("i am so happy")
    assert r.mood == "happy" and 0 < r.score <= 1 and 0 < r.confidence <= 1


@pytest.mark.parametrize("text, found, rest", [
    ("emo, add milk to my list", True, "add milk to my list"),
    ("hey emo what time is it", True, "what time is it"),
    ("Emo.", True, ""),
    ("remove the milk", False, None),
    ("take a memo", False, None),
    ("i feel emotional", False, None),
    ("emotion", False, None),
])
def test_wake_word_is_a_whole_word(text, found, rest):
    got_found, got_rest = split_wake_word(text, ["emo"])
    assert got_found is found
    if found:
        assert got_rest == rest


def test_wake_word_in_voice_session_one_breath(tmp_path):
    a, io, store = make(tmp_path, inputs=[("remove the milk", False), ("take a memo", False),
                                          ("emo, add milk to my list", False)])
    a.run()
    assert store.todos() == ["milk"]
    assert "I'm listening!" not in io.said


REMINDER_CASES = [
    ("remind me to call mom at 5:30 pm", "call mom", datetime(2026, 11, 20, 17, 30)),
    ("remind me to call mom at 5:30 p.m.", "call mom", datetime(2026, 11, 20, 17, 30)),
    ("remind me to stretch at 17:45", "stretch", datetime(2026, 11, 20, 17, 45)),
    ("remind me to eat at 5 30 pm", "eat", datetime(2026, 11, 20, 17, 30)),
    ("remind me to eat at 5", "eat", datetime(2026, 11, 20, 17, 0)),        # "eat" contains "at"
    ("remind me to eat at 3", "eat", datetime(2026, 11, 20, 15, 0)),        # next sensible 3 o'clock
    ("remind me to nap at 1", "nap", datetime(2026, 11, 21, 13, 0)),        # 1 PM passed -> tomorrow, not 1 AM
    ("remind me that the bus is at 4 pm", "the bus is", datetime(2026, 11, 20, 16, 0)),
    ("remind me in 10 minutes to drink water", "drink water", datetime(2026, 11, 20, 14, 10)),
    ("remind me in five minutes to check the oven", "check the oven", datetime(2026, 11, 20, 14, 5)),
    ("remind me in twenty five minutes to stand up", "stand up", datetime(2026, 11, 20, 14, 25)),
    ("remind me to stretch in half an hour", "stretch", datetime(2026, 11, 20, 14, 30)),
    ("remind me to walk in an hour and a half", "walk", datetime(2026, 11, 20, 15, 30)),
    ("remind me to call the dentist tomorrow at 9", "call the dentist", datetime(2026, 11, 21, 9, 0)),
    ("tomorrow at 3 remind me to pay rent", "pay rent", datetime(2026, 11, 21, 15, 0)),
    ("remind me at noon to eat lunch", "eat lunch", datetime(2026, 11, 21, 12, 0)),
    ("remind me at midnight to sleep", "sleep", datetime(2026, 11, 21, 0, 0)),
    ("remind me tonight to call dad", "call dad", datetime(2026, 11, 20, 20, 0)),
    ("remind me tonight at 9 to read", "read", datetime(2026, 11, 20, 21, 0)),
    ("at 5 pm remind me to call mom", "call mom", datetime(2026, 11, 20, 17, 0)),
    ("remind me to write a letter at 5 pm", "write a letter", datetime(2026, 11, 20, 17, 0)),
    ("remind me tomorrow morning to run", "run", datetime(2026, 11, 21, 9, 0)),
    ("remind me on monday to call grandma", "call grandma", datetime(2026, 11, 23, 9, 0)),
]


@pytest.mark.parametrize("text, message, due", REMINDER_CASES)
def test_parse_reminder(text, message, due):
    assert parse_reminder(text, NOW) == (message, due)


@pytest.mark.parametrize("text", ["remind me to look at that", "remind me to buy 2 apples", "remind me to call mom"])
def test_parse_reminder_without_a_time(text):
    assert parse_reminder(text, NOW) is None


def test_missing_reminder_time_is_asked_once(tmp_path):
    a, io, store = make(tmp_path, inputs=["in ten minutes"])
    a.handle("remind me to stretch")
    assert sum("When should I remind you" in s for s in io.said) == 1
    (r,) = store.reminders()
    assert r.message == "stretch" and r.due == NOW + timedelta(minutes=10)

    a, io, store = make(tmp_path / "2", inputs=["whenever"])
    a.handle("remind me to stretch")
    assert sum("When should I remind you" in s for s in io.said) == 1
    assert store.reminders() == []
    assert "didn't set the reminder" in io.said[-1]


def test_say_time_and_minutes():
    assert say_time(NOW + timedelta(minutes=10), NOW) == "in 10 minutes"
    assert say_time(datetime(2026, 11, 20, 17, 30), NOW) == "at 5:30 PM"
    assert say_time(datetime(2026, 11, 21, 9, 0), NOW) == "tomorrow at 9 AM"
    assert format_clock(datetime(2026, 11, 21, 12, 0)) == "noon"
    assert parse_minutes("45 minutes") == 45
    assert parse_minutes("an hour and a half") == 90
    assert parse_minutes("twenty") == 20
    assert parse_minutes("half an hour") == 30


def test_open_file_asks_only_once(tmp_path):
    a, io, store = make(tmp_path, inputs=["notes.txt"])
    target = tmp_path / "notes.txt"
    a.system.file = target
    a.handle("open file")
    assert io.said.count("What file should I open?") == 1
    assert a.system.opened == [target]
    assert io.said[-1] == "Opening notes.txt."


def test_open_file_with_name_does_not_ask(tmp_path):
    a, io, _ = make(tmp_path)
    a.system.file = tmp_path / "budget.xlsx"
    a.handle("open file budget.xlsx")
    assert "What file should I open?" not in io.said


def test_dispatch_table_covers_every_intent_once(tmp_path):
    a, io, _ = make(tmp_path)
    assert set(a.commands) == set(INTENT_NAMES)
    assert len(INTENT_NAMES) == len(set(INTENT_NAMES))
    for phrase in ("fortune", "fun mode"):
        io.said.clear()
        a.handle(phrase)
        assert len(io.said) == 1


INTENT_CASES = [
    ("open youtube despacito", "youtube", {"query": "despacito"}),
    ("open youtube", "youtube", {"query": ""}),
    ("play lofi beats on youtube", "youtube", {"query": "lofi beats"}),
    ("open word", "open_word", {}),
    ("open file", "open_file", {"name": ""}),
    ("open file notes.txt", "open_file", {"name": "notes.txt"}),
    ("write buy a charger", "write_note", {"text": "buy a charger"}),
    ("remind me to write a letter at 5 pm", "remind", None),
    ("add milk to my list", "todo_add", {"item": "milk"}),
    ("add eggs to my to-do list", "todo_add", {"item": "eggs"}),
    ("add bread", "todo_add", {"item": "bread"}),
    ("remove milk from my list", "todo_remove", {"item": "milk"}),
    ("remove number 2", "todo_remove", {"item": "number 2"}),
    ("what's on my list", "todo_show", {}),
    ("show list", "todo_show", {}),
    ("clear my list", "todo_clear", {}),
    ("play music", "play_music", {}),
    ("put on some music", "play_music", {}),
    ("stop the music", "stop_music", {}),
    ("stop music", "stop_music", {}),
    ("show my mood chart", "mood_chart", {}),
    ("motivate me", "motivate", {}),
    ("fortune", "fortune", {}),
    ("fun mode", "fun_mode", {}),
    ("tell me a joke", "joke", {}),
    ("start focus for 45 minutes", "start_focus", {"duration": "45 minutes"}),
    ("focus for 20 minutes", "start_focus", {"duration": "20 minutes"}),
    ("stop focus", "stop_focus", {}),
    ("show my reminders", "show_reminders", {}),
    ("cancel my reminders", "cancel_reminders", {}),
    ("i'm grateful for my sister", "gratitude_add", {"what": "my sister"}),
    ("read my gratitude", "gratitude_read", {}),
    ("how was my week", "weekly_recap", {}),
    ("breathing exercise", "breathing", {}),
    ("calm me down", "breathing", {}),
    ("go to sleep", "sleep", {}),
    ("help", "help", {}),
    ("what time is it", "time", {}),
    ("what's the date", "date", {}),
    ("read my notes", "read_notes", {}),
    ("good morning", "greeting", {}),
    ("address the letter", None, None),
    ("i removed the milk", None, None),
    ("i'm sad", None, None),
    ("what is the weather", None, None),
]


@pytest.mark.parametrize("text, name, args", INTENT_CASES)
def test_parse_intent(text, name, args):
    intent = parse_intent(text)
    if name is None:
        assert intent is None
    else:
        assert intent is not None and intent.name == name
        if args is not None:
            assert intent.args == args


def test_reminders_take_priority_over_notes(tmp_path):
    a, io, store = make(tmp_path)
    a.handle("remind me to write a letter at 5 pm")
    assert [r.message for r in store.reminders()] == ["write a letter"]
    assert store.notes() == []


def test_add_and_remove_are_whole_words(tmp_path):
    a, io, store = make(tmp_path)
    store.add_todo("milk")
    a.handle("address the letter")
    a.handle("i removed the milk yesterday")
    assert store.todos() == ["milk"]
    assert contains_phrase("please add milk", "add") and not contains_phrase("address", "add")


def test_bare_remove_never_wipes_the_list(tmp_path):
    a, io, store = make(tmp_path, inputs=[""])
    store.add_todo("milk")
    store.add_todo("eggs")
    a.handle("remove")
    assert store.todos() == ["milk", "eggs"]


def test_notes_are_one_per_line_with_timestamp(tmp_path):
    a, io, store = make(tmp_path)
    a.handle("write buy a charger")
    a.handle("write call the bank")
    lines = store.notes_file.read_text().splitlines()
    assert lines == ["2026-11-20 14:00  buy a charger", "2026-11-20 14:00  call the bank"]


def test_start_twice_runs_only_one_session():
    from emovoice.gui import SessionController

    release = threading.Event()
    created = []

    class SlowAssistant:
        def run(self):
            release.wait(5)

        def stop(self):
            release.set()

    def factory():
        created.append(SlowAssistant())
        return created[-1]

    events = queue.Queue()
    controller = SessionController(factory, events)
    assert controller.start() is True
    assert controller.start() is False
    assert len(created) == 1 and controller.running
    controller.stop()
    controller._thread.join(2)
    assert not controller.running
    assert controller.start() is True
    controller.stop()


def test_reminder_from_background_thread_goes_through_event_queue(tmp_path):
    events = queue.Queue()
    io = VoiceIO(Settings(), on_event=events.put, use_mic=False, use_tts=False, shows_images=True)
    store = Store(tmp_path)
    a = Assistant(io, store, Settings(), system=FakeSystem(), player=FakePlayer(), clock=lambda: NOW)
    store.add_reminder("call my mom", NOW - timedelta(seconds=1), NOW - timedelta(minutes=5))
    worker = threading.Thread(target=a.scheduler.check_now)
    worker.start()
    worker.join(5)
    items = []
    while not events.empty():
        items.append(events.get())
    kinds = [k for k, _ in items]
    assert "reminder" in kinds and "say" in kinds
    assert dict(items)["reminder"]["message"] == "call your mom"
    gui_source = (ROOT / "emovoice" / "gui.py").read_text(encoding="utf-8")
    assert "messagebox" not in gui_source


def test_music_player_is_non_blocking_and_stoppable(monkeypatch):
    state = {"busy": False}
    music = types.SimpleNamespace(load=lambda p: None, play=lambda: state.update(busy=True),
                                  stop=lambda: state.update(busy=False), get_busy=lambda: state["busy"])
    fake_pygame = types.SimpleNamespace(mixer=types.SimpleNamespace(get_init=lambda: True, init=lambda: None,
                                                                    music=music))
    monkeypatch.setitem(sys.modules, "pygame", fake_pygame)
    player = MusicPlayer()
    assert player.play("calm.mp3") == "pygame"
    assert player.is_playing
    assert player.stop() is True
    assert not player.is_playing


def test_playsound_fallback_runs_in_a_daemon_thread(monkeypatch):
    monkeypatch.setattr(MusicPlayer, "_play_pygame", lambda self, p: False)
    monkeypatch.setattr(MusicPlayer, "_play_process", lambda self, p: False)
    monkeypatch.setitem(sys.modules, "playsound", types.SimpleNamespace(playsound=lambda p: time.sleep(2)))
    player = MusicPlayer()
    start = time.monotonic()
    assert player.play("calm.mp3") == "playsound"
    assert time.monotonic() - start < 0.5
    assert player._thread.daemon and player.is_playing


def test_stop_music_command_does_not_quit(tmp_path):
    a, io, _ = make(tmp_path)
    a.handle("play music")
    assert a.player.is_playing
    assert a.handle("stop the music") is True
    assert not a.player.is_playing
    assert io.said[-1] == "Music stopped."


def test_mood_chart_is_last_7_days_with_fresh_figures(tmp_path):
    import matplotlib.pyplot as plt

    entries = [MoodEntry(NOW - timedelta(days=d), m) for d, m in
               [(0, "happy"), (0, "sad"), (1, "happy"), (6, "angry"), (7, "happy"), (30, "sad")]]
    days, counts = visuals.last_7_days(entries, NOW)
    assert days[-1] == NOW.date() and days[0] == NOW.date() - timedelta(days=6)
    assert sum(sum(v) for v in counts.values()) == 4
    assert counts["happy"][-1] == 1 and counts["sad"][-1] == 1 and counts["angry"][0] == 1

    for _ in range(2):
        path = visuals.mood_chart(entries, NOW, tmp_path / "chart.png")
        assert plt.get_fignums() == []
    assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    import matplotlib
    assert matplotlib.get_backend().lower() == "agg"


# =========================================================================== section 2: robustness

def test_tts_runs_on_one_worker_thread():
    idents = []

    class FakeEngine:
        def setProperty(self, *a):
            pass

        def say(self, text):
            idents.append(threading.get_ident())

        def runAndWait(self):
            idents.append(threading.get_ident())

    io = VoiceIO(Settings(), use_mic=False, engine_factory=FakeEngine)
    threads = [threading.Thread(target=io.say, args=(f"hello {i}",)) for i in range(4)]
    for t in threads:
        t.start()
    io.say("from main")
    for t in threads:
        t.join(5)
    assert len(idents) == 10
    assert len(set(idents)) == 1 and idents[0] != threading.get_ident()


def test_corrupt_json_is_quarantined(tmp_path):
    store = Store(tmp_path)
    store.moods_file.write_text("{not json")
    store.reminders_file.write_text('{"a": 1}')  # wrong shape
    assert store.moods() == []
    assert store.reminders() == []
    assert (tmp_path / "mood_history.json.corrupt").exists()
    assert (tmp_path / "reminders.json.corrupt").exists()
    store.add_mood("happy", NOW)
    assert [e.mood for e in store.moods()] == ["happy"]


def test_writes_are_atomic(tmp_path, monkeypatch):
    store = Store(tmp_path)
    store.add_mood("happy", NOW)
    before = store.moods_file.read_text()

    def boom(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr("emovoice.storage.os.replace", boom)
    with pytest.raises(OSError):
        store.add_mood("sad", NOW)
    assert store.moods_file.read_text() == before
    assert not list(tmp_path.glob("*.tmp"))


def test_a_failing_command_does_not_kill_the_loop(tmp_path):
    a, io, _ = make(tmp_path, inputs=["what time is it", "fortune"])

    def broken():
        raise RuntimeError("boom")
    a.commands["time"] = broken
    a.run()
    assert any("something went wrong" in s for s in io.said)
    assert any(s.startswith("Here's your fortune") for s in io.said)


def test_legacy_files_are_migrated(tmp_path):
    project, data = tmp_path, tmp_path / "data"
    data.mkdir()
    (data / "mood_history.json").write_text("")  # the empty v1 duplicate
    (project / "mood_history.json").write_text(json.dumps(
        [{"timestamp": "2025-07-30 20:28", "mood": "neutral"}, {"timestamp": "2025-07-31 00:06", "mood": "sad"}]))
    (project / "notes.txt").write_text("i love cod")
    actions = migrate_legacy(project, data)
    assert len(actions) == 2
    store = Store(data)
    assert [e.mood for e in store.moods()] == ["neutral", "sad"]
    assert store.notes()[0].endswith("i love cod")
    assert not (project / "mood_history.json").exists()
    assert (project / "mood_history.json.migrated").exists()
    assert migrate_legacy(project, data) == []  # idempotent


def test_old_mood_formats_are_readable(tmp_path):
    store = Store(tmp_path)
    store.moods_file.write_text(json.dumps([
        "sad", {"emotion": "Happy", "time": "2025-07-30 20:28"}, {"mood": "stressed", "timestamp": "2025-07-31T10:00:00"},
        42, {"mood": "confused"}, {"timestamp": "2025-07-31 10:00"}]))
    entries = store.moods()
    assert [e.mood for e in entries] == ["sad", "happy", "anxious"]
    assert entries[0].when is None and entries[1].when == datetime(2025, 7, 30, 20, 28)


def test_quote_image_uses_background_when_present(tmp_path):
    from PIL import Image
    bg = tmp_path / "bg.jpg"
    Image.new("RGB", (1200, 800), (220, 20, 20)).save(bg)
    with Image.open(visuals.quote_image("Stay strong.", tmp_path / "q1.png", bg)) as img:
        r, g, b = img.getpixel((5, 5))
    assert r > 2 * g and r > 2 * b
    with Image.open(visuals.quote_image("Stay strong.", tmp_path / "q2.png", None)) as img:
        r, g, b = img.getpixel((5, 5))
    assert b > r


def test_motivate_command_shows_image_in_app(tmp_path):
    a, io, _ = make(tmp_path)
    a.handle("motivate me")
    (image,) = io.kinds("image")
    assert Path(image["path"]).exists()
    assert a.system.opened == []  # the GUI shows it itself
    assert io.said[-1] in content.QUOTES


# =========================================================================== section 3: features

def test_text_io_session(tmp_path):
    lines = iter(["what's the date", "stop"])
    out = []
    io = TextIO(input_fn=lambda prompt: next(lines), print_fn=out.append, echo=False)
    a = Assistant(io, Store(tmp_path), Settings(user_name="Sam"), system=FakeSystem(), player=FakePlayer(),
                  clock=lambda: NOW)
    a.run()
    assert out[0].startswith("EmoVoice: Good afternoon, Sam!")
    assert "EmoVoice: Today is Friday, November 20, 2026." in out
    assert out[-1] == "EmoVoice: Goodbye! Take care."


def test_typed_input_skips_wake_word(tmp_path):
    a, io, _ = make(tmp_path, inputs=[("what time is it", True)])
    a.run()
    assert "It's 2 PM." in io.said


def test_reminders_persist_across_restarts(tmp_path):
    Store(tmp_path).add_reminder("water the plants", NOW + timedelta(hours=1), NOW)
    (r,) = Store(tmp_path).reminders()
    assert r.message == "water the plants" and r.due == NOW + timedelta(hours=1)


def test_missed_reminders_are_announced_at_startup(tmp_path):
    a, io, store = make(tmp_path)
    store.add_reminder("call my mom", NOW - timedelta(hours=2), NOW - timedelta(hours=3))
    store.add_reminder("later thing", NOW + timedelta(hours=2), NOW)
    a.run()
    assert "While you were away, you missed 1 reminder: call your mom at noon." in io.said
    assert [r.message for r in store.reminders()] == ["later thing"]


def test_show_and_cancel_reminders(tmp_path):
    a, io, store = make(tmp_path)
    a.handle("remind me to call mom at 5:30 pm")
    a.handle("remind me in 10 minutes to stretch")
    a.handle("show my reminders")
    assert io.said[-1] == "You have 2 reminders: stretch in 10 minutes; call mom at 5:30 PM."
    a.handle("cancel my reminders")
    assert io.said[-1] == "Cancelled 2 reminders."
    assert store.reminders() == []


def test_scheduler_fires_due_reminders(tmp_path):
    store = Store(tmp_path)
    fired = []
    sched = ReminderScheduler(store, fired.append, clock=lambda: NOW)
    store.add_reminder("a", NOW + timedelta(minutes=1), NOW)
    assert sched.check_now(NOW) == []
    sched.check_now(NOW + timedelta(minutes=1))
    assert [r.message for r in fired] == ["a"]
    assert store.reminders() == []


def test_focus_timer_counts_down_and_finishes_once():
    t = [1000.0]
    ticks, done = [], []
    timer = FocusTimer(lambda r, total: ticks.append(r), done.append, clock=lambda: t[0])
    timer.start(1, threaded=False)
    t[0] += 30
    assert timer.tick() == 30
    t[0] += 30
    assert timer.tick() == 0
    assert done == [60] and not timer.running
    timer.tick()
    assert done == [60]
    assert ticks[0] == 60


def test_focus_commands(tmp_path):
    a, io, _ = make(tmp_path)
    a.handle("start focus for 45 minutes")
    assert a.focus.running and a.focus.total == 45 * 60
    assert io.kinds("focus")[0]["remaining"] == 2700
    a.handle("stop focus")
    assert not a.focus.running
    assert io.said[-1].startswith("Focus session stopped")
    a.handle("stop focus")
    assert io.said[-1] == "No focus session is running."


def test_gratitude_journal_and_sad_reminder(tmp_path):
    a, io, store = make(tmp_path)
    a.handle("I'm grateful for my sister and my dog")
    assert io.said[-1] == "That's lovely. I've saved that you're grateful for your sister and your dog."
    a.handle("read my gratitude")
    assert "your sister and your dog" in io.said[-1]
    a.handle("i am sad")
    assert "you're grateful for your sister and your dog" in io.said[-1]
    assert content.second_person("my mom and me") == "your mom and you"


def test_weekly_mood_recap(tmp_path):
    store = Store(tmp_path)
    for days_ago, mood in [(9, "sad"), (10, "sad"), (11, "angry"), (3, "sad"), (2, "happy"), (2, "happy"), (0, "happy")]:
        store.add_mood(mood, NOW - timedelta(days=days_ago))
    s = visuals.mood_summary(store.moods(), NOW)
    assert s["count"] == 4 and s["previous_count"] == 3
    assert s["dominant"] == "happy"
    assert s["happiest_day"] == (NOW - timedelta(days=2)).date()
    assert s["trend"] == "up"
    a, io, _ = make(tmp_path / "x")
    a.store = store
    a.handle("how was my week")
    said = io.said[-1]
    assert "4 times" in said and "happy" in said and "Wednesday" in said and "trending up" in said


def test_breathing_exercise_uses_injected_sleep(tmp_path):
    a, io, _ = make(tmp_path)
    a.handle("breathing exercise")
    assert a.sleeps == [4] * 12


@pytest.mark.parametrize("phrase", ["i am so angry", "i am stressed about work"])
def test_breathing_runs_automatically_for_angry_and_anxious(tmp_path, phrase):
    a, io, _ = make(tmp_path)
    a.handle(phrase)
    assert a.sleeps == [4] * 8
    assert any(s.startswith("Breathe in") for s in io.said)


def test_wellbeing_nudge_after_three_low_moods(tmp_path):
    a, io, _ = make(tmp_path)
    for phrase in ("i am sad", "i feel lonely", "i am so stressed"):
        a.handle(phrase)
    assert io.said.count(content.NUDGE) == 1
    a.handle("i am miserable")
    assert io.said.count(content.NUDGE) == 1  # once per day


def test_sleep_mode_and_stop(tmp_path):
    a, io, _ = make(tmp_path, inputs=[("emo", False), ("what time is it", False), ("go to sleep", False),
                                      ("what time is it", False), ("emo what time is it", False),
                                      ("stop the music", False), ("stop", False), ("fortune", False)])
    a.run()
    assert io.said.count("It's 2 PM.") == 2
    assert not any(s.startswith("Here's your fortune") for s in io.said)  # "stop" quit before it
    assert io.said[-1] == "Goodbye! Take care."
    assert is_exact_command("please stop", ["stop"]) and not is_exact_command("stop the music", ["stop"])


def test_extras(tmp_path):
    a, io, store = make(tmp_path, inputs=["no", "yes"], settings=Settings(user_name="Sam"))
    a.handle("help")
    assert io.kinds("help") and io.said[-1] == content.HELP_SHORT
    a.handle("what time is it")
    assert io.said[-1] == "It's 2 PM."
    a.handle("what's the date")
    assert io.said[-1] == "Today is Friday, November 20, 2026."
    a.handle("good morning")
    assert io.said[-1] == "Good afternoon, Sam! How are you feeling?"
    a.handle("write remember the milk")
    a.handle("read my notes")
    assert io.said[-1] == "Here is your latest note: remember the milk."
    for item in ("milk", "eggs", "oat milk", "bread"):
        store.add_todo(item)
    a.handle("remove number 2")
    assert store.todos() == ["milk", "oat milk", "bread"]
    a.handle("remove brea")
    assert store.todos() == ["milk", "oat milk"]
    a.handle("remove the last one")
    assert store.todos() == ["milk"]
    store.add_todo("oat milk")
    a.handle("remove mil")  # ambiguous partial match: nothing removed
    assert "Several items match" in io.said[-1] and store.todos() == ["milk", "oat milk"]
    a.handle("clear my list")  # answered "no"
    assert store.todos() == ["milk", "oat milk"]
    a.handle("clear my list")  # answered "yes"
    assert store.todos() == []
    assert parse_index("the second one") == 2 and parse_index("item three") == 3 and parse_index("milk") is None


def test_settings_file_and_name_flag(tmp_path):
    from emovoice.__main__ import build_parser, build_settings
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"user_name": "Ana", "wake_words": ["emo", "Emma"], "speech_rate": "fast",
                                "focus_minutes": 50, "history_limit": -3, "unknown": 1}))
    s = load_settings(path)
    assert s.user_name == "Ana" and s.wake_words == ["emo", "emma"] and s.focus_minutes == 50
    assert s.speech_rate == 160 and s.history_limit == 1000  # invalid values ignored
    args = build_parser().parse_args(["--settings", str(path), "--name", "Sam"])
    assert build_settings(args).user_name == "Sam"
    path.write_text("{broken")
    assert load_settings(path) == Settings()
    assert json.loads((ROOT / "settings.example.json").read_text())  # example file is valid JSON


def test_focus_uses_settings_default(tmp_path):
    a, io, _ = make(tmp_path, settings=Settings(focus_minutes=30))
    a.handle("start focus")
    assert a.focus.total == 30 * 60
    a.focus.stop()


def test_gui_smoke(tmp_path):
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    try:
        from emovoice.gui import EmoVoiceApp
        app = EmoVoiceApp(root, Settings(), Store(tmp_path), use_mic=False, use_tts=False)
        # background thread only enqueues; the main thread applies the change
        worker = threading.Thread(target=lambda: (app.events.put(("say", {"text": "hello there"})),
                                                  app.events.put(("mood", {"mood": "happy", "emoji": "😊"})),
                                                  app.events.put(("reminder", {"message": "drink water"})),
                                                  app.events.put(("focus", {"remaining": 125, "total": 1500}))))
        worker.start()
        worker.join()
        assert app.process_events() == 4
        assert "hello there" in app.chat.get("1.0", "end")
        assert "Happy" in app.mood_badge.cget("text")
        assert app.focus_label.cget("text") == "02:05"
        assert len(app._toasts) == 1
        visuals.quote_image("Hi", tmp_path / "q.png")
        app.events.put(("image", {"path": str(tmp_path / "q.png"), "title": "Motivation"}))
        app.process_events()
        assert app._photo is not None
        assert app.start() is True and app.start() is False
        assert str(app.start_button.cget("state")) == "disabled"
        app.controller.stop()
        app.controller._thread.join(5)
        app.process_events()
        assert str(app.start_button.cget("state")) == "normal"
    finally:
        root.destroy()
