"""The assistant: session loop, routing, and one cmd_* method per intent.

Every utterance gets exactly one handler: a matching intent, or else the mood
responder. Handlers never touch UI toolkits; they only call self.io.
"""

from __future__ import annotations

import logging
import random
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from . import content, visuals
from .config import Settings, find_asset, search_dirs
from .intents import (INTENT_NAMES, is_exact_command, parse_index, parse_intent,
                      split_wake_word, strip_leading_wake_word)
from .mood import LOW_MOODS, detect_mood, is_feeling_statement
from .reminders import FocusTimer, ReminderScheduler
from .speech import IO
from .storage import Reminder, Store
from .system import YOUTUBE_URL, MusicPlayer, System
from .timeparse import format_clock, parse_minutes, parse_reminder, parse_time, reminder_message, say_time

log = logging.getLogger(__name__)

MOOD_EMOJI = {"happy": "😊", "sad": "😢", "angry": "😠", "anxious": "😟", "neutral": "😐"}
_YES = re.compile(r"^(?:yes|yeah|yep|yup|sure|ok|okay|do it|go ahead|please|confirm|absolutely)\b")
_CANCEL = re.compile(r"^(?:cancel|never\s?mind|forget it|no|nope|stop)$")


class Assistant:
    def __init__(self, io: IO, store: Store, settings: Settings | None = None, *,
                 system: System | None = None, player: MusicPlayer | None = None,
                 clock: Callable[[], datetime] = datetime.now, sleep: Callable[[float], None] = time.sleep,
                 rng: random.Random | None = None, scheduler_interval: float = 1.0):
        self.io = io
        self.store = store
        self.settings = settings or Settings()
        self.system = system or System()
        self.player = player or MusicPlayer()
        self.clock = clock
        self.sleep = sleep
        self.rng = rng or random.Random()
        self.awake = False
        self._stop = threading.Event()
        self._nudged_on = None
        self.scheduler = ReminderScheduler(store, self._on_reminder, clock=clock, interval=scheduler_interval)
        self.focus = FocusTimer(self._on_focus_tick, self._on_focus_done)
        self.commands: dict[str, Callable[..., None]] = {name: getattr(self, f"cmd_{name}") for name in INTENT_NAMES}

    @property
    def data_dir(self) -> Path:
        return self.store.data_dir

    # ------------------------------------------------------------------ session
    def run(self) -> None:
        self._stop.clear()
        self.io.open()
        self.awake = False
        try:
            self._announce_missed()
            self.scheduler.start()
            self.io.event("status", state="sleeping")
            wake = self.settings.wake_words[0].capitalize() if self.settings.wake_words else "Emo"
            self.io.say(f"{content.greeting(self.settings.user_name, self.clock())} I'm EmoVoice. "
                        f"Say '{wake}' to wake me, type a message, or say 'help'.")
            while not self._stop.is_set():
                utterance = self.io.listen()
                if utterance is None:
                    break
                text = utterance.text.strip()
                if not utterance.typed and not self.awake:
                    found, rest = split_wake_word(text, self.settings.wake_words)
                    if not found:
                        continue
                    self._set_awake(True)
                    if not rest:
                        self.io.say("I'm listening!")
                        continue
                    text = rest
                elif not text:
                    if self.awake and not utterance.typed:
                        self.io.say("I didn't catch that. Try again.")
                    continue
                if not self.handle(text):
                    break
        finally:
            self.scheduler.stop()
            self.focus.stop()
            self.player.stop()
            self.io.say("Goodbye! Take care.")
            self.io.event("status", state="stopped")
            self.io.event("stopped")

    def stop(self) -> None:
        """Thread-safe request to end the session."""
        self._stop.set()
        self.io.close()

    def _set_awake(self, awake: bool) -> None:
        self.awake = awake
        self.io.event("status", state="listening" if awake else "sleeping")

    def handle(self, text: str) -> bool:
        """Respond to one utterance. Returns False when the user asked to quit."""
        text = strip_leading_wake_word(text, self.settings.wake_words)
        if not text:
            return True
        self.io.event("user", text=text)
        if is_exact_command(text, self.settings.stop_words):
            return False
        intent = parse_intent(text)
        try:
            if intent:
                self.commands[intent.name](**intent.args)
            else:
                self.respond_to_mood(text)
        except Exception:
            log.exception("Command failed: %r -> %r", text, intent)
            self.io.say("Sorry, something went wrong with that. Let's try something else.")
        return True

    def ask(self, question: str) -> str:
        """Ask one follow-up question; '' if there was no usable answer."""
        self.io.say(question)
        answer = self.io.listen(timeout=self.settings.answer_timeout)
        if answer is None:
            self._stop.set()
            return ""
        text = strip_leading_wake_word(answer.text, self.settings.wake_words)
        return "" if _CANCEL.match(text) else text

    def _show_image(self, path: Path, title: str) -> None:
        self.io.event("image", path=str(path), title=title)
        if not self.io.shows_images and self.settings.open_images:
            self.system.open_path(path)

    # ------------------------------------------------------------------ mood
    def respond_to_mood(self, text: str) -> None:
        result = detect_mood(text)
        now = self.clock()
        if result.mood == "neutral" and not is_feeling_statement(text):
            self.io.say(content.UNKNOWN)
            return
        self.store.add_mood(result.mood, now, result.score)
        self.io.event("mood", mood=result.mood, emoji=MOOD_EMOJI[result.mood], confidence=result.confidence)
        reply = self.rng.choice(content.MOOD_REPLIES[result.mood])

        if result.mood == "sad":
            message = f"{reply} {content.joke(self.rng)}"
            memory = self._gratitude_memory()
            if memory:
                message += f" And remember, you once told me you're grateful for {memory}."
            self.io.say(message)
        elif result.mood == "angry":
            self.io.say(reply)
            self._play_calm_music(announce=False)
            self.breathe(cycles=2)
        elif result.mood == "anxious":
            self.io.say(reply)
            self.breathe(cycles=2)
        else:
            self.io.say(reply)

        if result.mood in LOW_MOODS:
            self._maybe_nudge(now)

    def _gratitude_memory(self) -> str | None:
        entries = self.store.gratitude()
        if not entries:
            return None
        return content.second_person(self.rng.choice(entries).text)

    def _maybe_nudge(self, now: datetime) -> None:
        if self._nudged_on == now.date():
            return
        low_today = sum(1 for e in self.store.moods()
                        if e.when and e.when.date() == now.date() and e.mood in LOW_MOODS)
        if low_today >= 3:
            self._nudged_on = now.date()
            self.io.say(content.NUDGE)

    def breathe(self, cycles: int = 3) -> None:
        """Box breathing: in 4, hold 4, out 4, hold 4."""
        self.io.event("status", state="breathing")
        for _ in range(cycles):
            for step in ("Breathe in", "Hold", "Breathe out", "Hold"):
                if self._stop.is_set():
                    return
                self.io.say(f"{step}... two, three, four.")
                self.sleep(4)
        self.io.say("Well done. Notice how your body feels now.")
        self.io.event("status", state="listening" if self.awake else "sleeping")

    # ------------------------------------------------------------------ simple commands
    def cmd_help(self) -> None:
        self.io.event("help", text=content.HELP_TEXT)
        self.io.say(content.HELP_SHORT)

    def cmd_greeting(self) -> None:
        self.io.say(f"{content.greeting(self.settings.user_name, self.clock())} How are you feeling?")

    def cmd_sleep(self) -> None:
        self._set_awake(False)
        wake = self.settings.wake_words[0].capitalize() if self.settings.wake_words else "Emo"
        self.io.say(f"Okay, I'll rest. Say '{wake}' when you need me.")

    def cmd_time(self) -> None:
        self.io.say(f"It's {format_clock(self.clock())}.")

    def cmd_date(self) -> None:
        now = self.clock()
        self.io.say(f"Today is {now:%A, %B} {now.day}, {now.year}.")

    def cmd_fortune(self) -> None:
        self.io.say(f"Here's your fortune: {self.rng.choice(content.FORTUNES)}")

    def cmd_fun_mode(self) -> None:
        self.io.say(f"Fun mode activated! {self.rng.choice(content.FUN_REPLIES)}")

    def cmd_joke(self) -> None:
        self.io.say(content.joke(self.rng))

    def cmd_breathing(self) -> None:
        self.io.say("Let's do some box breathing together. Get comfortable.")
        self.breathe(cycles=3)

    # ------------------------------------------------------------------ apps & media
    def cmd_youtube(self, query: str = "") -> None:
        if query:
            self.io.say(f"Playing {query} on YouTube.")
            ok = self.system.play_youtube(query)
        else:
            self.io.say("Opening YouTube.")
            ok = self.system.open_url(YOUTUBE_URL)
        if not ok:
            self.io.say("I couldn't open the browser.")

    def cmd_open_word(self) -> None:
        if self.system.open_word():
            self.io.say("Opening your word processor.")
        else:
            self.io.say("I couldn't find Microsoft Word or LibreOffice on this computer.")

    def cmd_open_file(self, name: str = "") -> None:
        if not name:
            name = self.ask("What file should I open?")
        if not name:
            self.io.say("I didn't catch the file name.")
            return
        path = self.system.find_file(name, search_dirs(self.data_dir))
        if path and self.system.open_path(path):
            self.io.say(f"Opening {path.name}.")
        else:
            self.io.say(f"I couldn't find a file called {name}. Please check the name.")

    def _play_calm_music(self, announce: bool = True) -> None:
        path = find_asset(self.settings.music_file)
        if not path:
            if announce:
                self.io.say(f"I couldn't find the music file {self.settings.music_file}.")
            return
        if announce:
            self.io.say("Playing calming music. Say 'stop music' whenever you like.")
        self.player.play(path)

    def cmd_play_music(self) -> None:
        self._play_calm_music()

    def cmd_stop_music(self) -> None:
        if self.player.is_playing:
            stopped = self.player.stop()
            self.io.say("Music stopped." if stopped else "Sorry, this audio player can't be stopped early.")
        else:
            self.io.say("No music is playing.")

    def cmd_motivate(self) -> None:
        quote = self.rng.choice(content.QUOTES)
        background = find_asset("motivational_bg.jpg")
        path = visuals.quote_image(quote, self.data_dir / "motivational_quote.png", background)
        self._show_image(path, "Motivation")
        self.io.say(quote)

    # ------------------------------------------------------------------ notes & to-dos
    def cmd_write_note(self, text: str = "") -> None:
        if not text:
            text = self.ask("What should I write down?")
        if not text:
            self.io.say("I didn't catch anything to write.")
            return
        self.store.add_note(text, self.clock())
        self.io.say("Written to your notes.")

    def cmd_read_notes(self) -> None:
        notes = self.store.notes()
        if not notes:
            self.io.say("You don't have any notes yet. Say 'write' followed by your note.")
            return
        latest = notes[-5:]
        spoken = "; ".join(re.sub(r"^\d{4}-\d\d-\d\d \d\d:\d\d\s+", "", n) for n in latest)
        self.io.say(f"Here {'is your latest note' if len(latest) == 1 else f'are your latest {len(latest)} notes'}: {spoken}.")

    def cmd_todo_add(self, item: str = "") -> None:
        if not item:
            item = self.ask("What should I add to your list?")
        if not item:
            self.io.say("I didn't catch what to add.")
            return
        self.store.add_todo(item)
        self.io.say(f"Added {item} to your to-do list.")

    def cmd_todo_remove(self, item: str = "") -> None:
        if not self.store.todos():
            self.io.say("Your to-do list is already empty.")
            return
        if not item:
            item = self.ask("Which item should I remove? You can say its number.")
        if not item:
            self.io.say("I didn't catch which item to remove.")
            return
        index = parse_index(item)
        removed, matches = self.store.remove_todo(index if index is not None else item)
        if removed:
            self.io.say(f"Removed {removed} from your list.")
        elif len(matches) > 1:
            self.io.say(f"Several items match {item}: {', '.join(matches)}. Say 'remove number' and the item number.")
        else:
            self.io.say(f"I couldn't find {item} on your list.")

    def cmd_todo_show(self) -> None:
        items = self.store.todos()
        if not items:
            self.io.say("Your to-do list is empty.")
            return
        listed = ". ".join(f"{i}: {item}" for i, item in enumerate(items, 1))
        self.io.say(f"You have {len(items)} item{'s' if len(items) != 1 else ''} on your list. {listed}.")

    def cmd_todo_clear(self) -> None:
        count = len(self.store.todos())
        if not count:
            self.io.say("Your to-do list is already empty.")
            return
        answer = self.ask(f"Clear all {count} item{'s' if count != 1 else ''} from your list? Say yes to confirm.")
        if _YES.match(answer):
            self.store.clear_todos()
            self.io.say("Your to-do list is now empty.")
        else:
            self.io.say("Okay, I kept your list.")

    # ------------------------------------------------------------------ reminders
    def cmd_remind(self, text: str = "") -> None:
        now = self.clock()
        parsed = parse_reminder(text, now)
        if parsed:
            message, due = parsed
        else:
            message = reminder_message(text)
            answer = self.ask(f"When should I remind you{' to ' + content.second_person(message) if message else ''}?")
            due = parse_time(answer, self.clock()) if answer else None
            if not due:
                self.io.say("Sorry, I couldn't understand the time, so I didn't set the reminder.")
                return
        if not message:
            message = "your reminder"
        self.store.add_reminder(message, due, now)
        self.io.say(f"Okay, I'll remind you to {content.second_person(message)} {say_time(due, now)}.")

    def cmd_show_reminders(self) -> None:
        items = self.store.reminders()
        if not items:
            self.io.say("You have no reminders.")
            return
        now = self.clock()
        listed = "; ".join(f"{content.second_person(r.message)} {say_time(r.due, now)}" for r in items[:5])
        more = f" and {len(items) - 5} more" if len(items) > 5 else ""
        self.io.say(f"You have {len(items)} reminder{'s' if len(items) != 1 else ''}: {listed}{more}.")

    def cmd_cancel_reminders(self) -> None:
        count = self.store.clear_reminders()
        self.io.say(f"Cancelled {count} reminder{'s' if count != 1 else ''}." if count else "You have no reminders to cancel.")

    def _on_reminder(self, reminder: Reminder) -> None:
        text = content.second_person(reminder.message)
        self.io.event("reminder", message=text, due=reminder.due.isoformat(timespec="minutes"))
        self.io.say(f"Reminder: {text}.")

    def _announce_missed(self) -> None:
        missed = self.scheduler.collect_missed()
        if not missed:
            return
        items = "; ".join(f"{content.second_person(r.message)} at {format_clock(r.due)}"
                          + ("" if r.due.date() == self.clock().date() else f" on {r.due:%A}") for r in missed)
        self.io.event("reminder", message=f"Missed while you were away: {items}", due=None)
        self.io.say(f"While you were away, you missed {len(missed)} reminder{'s' if len(missed) != 1 else ''}: {items}.")

    # ------------------------------------------------------------------ focus
    def cmd_start_focus(self, duration: str = "") -> None:
        minutes = parse_minutes(duration) if duration else None
        minutes = minutes or self.settings.focus_minutes
        minutes = max(1, min(240, round(minutes)))
        self.focus.start(minutes)
        self.io.say(f"Starting a {minutes}-minute focus session. I'll tell you when it's time for a break.")

    def cmd_stop_focus(self) -> None:
        elapsed = self.focus.stop()
        if elapsed is None:
            self.io.say("No focus session is running.")
        else:
            minutes = elapsed // 60
            self.io.say(f"Focus session stopped after {minutes} minute{'s' if minutes != 1 else ''}.")

    def _on_focus_tick(self, remaining: int, total: int) -> None:
        self.io.event("focus", remaining=remaining, total=total, done=False)

    def _on_focus_done(self, total: int) -> None:
        self.io.event("focus", remaining=0, total=total, done=True)
        self.io.say(f"Great work! Your {total // 60}-minute focus session is done. Time for a short break.")

    # ------------------------------------------------------------------ gratitude & insight
    def cmd_gratitude_add(self, what: str = "") -> None:
        if not what:
            what = self.ask("What are you grateful for?")
        if not what:
            return
        self.store.add_gratitude(what, self.clock())
        self.io.say(f"That's lovely. I've saved that you're grateful for {content.second_person(what)}.")

    def cmd_gratitude_read(self) -> None:
        entries = self.store.gratitude()
        if not entries:
            self.io.say("Your gratitude journal is empty. Say 'I'm grateful for' and something good, any time.")
            return
        latest = [content.second_person(e.text) for e in entries[-3:]]
        self.io.say(f"You've said you're grateful for: {'; '.join(latest)}.")

    def cmd_weekly_recap(self) -> None:
        now = self.clock()
        self.io.say(visuals.summary_text(visuals.mood_summary(self.store.moods(), now), now))

    def cmd_mood_chart(self) -> None:
        entries = self.store.moods()
        now = self.clock()
        if not any(e.when and e.when >= now - timedelta(days=7) for e in entries):
            self.io.say("I don't have mood data from the last week yet. Tell me how you're feeling first.")
            return
        path = visuals.mood_chart(entries, now, self.data_dir / "mood_chart.png")
        self._show_image(path, "Mood chart")
        self.io.say("Here's your mood over the last 7 days.")
