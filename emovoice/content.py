"""Static text: jokes, fortunes, quotes, replies and help."""

from __future__ import annotations

import random
import re
from datetime import datetime

FALLBACK_JOKES = [
    "Why do programmers prefer dark mode? Because light attracts bugs.",
    "I told my computer I needed a break, and it said: no problem, I'll go to sleep.",
    "Why did the scarecrow win an award? He was outstanding in his field.",
    "What do you call a fake noodle? An impasta.",
]

FORTUNES = [
    "Today is a great day to start something new.",
    "Be kind to yourself and good things will happen.",
    "You might receive unexpected news today.",
    "Focus on your goals and stay consistent.",
    "Something joyful is coming your way.",
]

QUOTES = [
    "Believe in yourself!",
    "You are doing great!",
    "Keep pushing forward!",
    "Stay strong.",
    "Small steps every day add up to big results.",
    "You have survived every hard day so far.",
    "Progress, not perfection.",
]

FUN_REPLIES = [
    "Well well well, look who's back!",
    "I'm your funky assistant now, ready to groove!",
    "Did someone order sass with a side of code?",
]

MOOD_REPLIES = {
    "happy": [
        "It's good to hear you happy! I'm always here for you.",
        "Love that energy! Keep it going.",
        "That's wonderful. Moments like this are worth savouring.",
    ],
    "sad": [
        "You sound a bit down. Here's something to cheer you up:",
        "I'm sorry you're feeling low. Maybe this will help a little:",
    ],
    "angry": [
        "You sound frustrated. Let's slow down and breathe together.",
        "That sounds really annoying. Let's take a moment to breathe.",
    ],
    "anxious": [
        "It sounds like you're feeling stressed. Let's do some box breathing together.",
        "That sounds like a lot to carry. Let's breathe through it together.",
    ],
    "neutral": [
        "Thanks for checking in. How can I help?",
        "Got it. Let me know if there's anything I can do.",
    ],
}

NUDGE = ("I've noticed today has been a tough one. It can really help to talk with someone "
         "you trust, like a friend or someone in your family. I'm here for you too.")

UNKNOWN = "I'm not sure how to help with that yet. Say 'help' to hear what I can do."

HELP_SHORT = ("I can manage your to-do list, notes and reminders, play music or YouTube, run a focus timer, "
              "lead a breathing exercise, keep a gratitude journal, and track your mood. "
              "The full list of commands is on screen.")

HELP_TEXT = """\
Things you can say (start with "Emo" when using your voice):

  Mood         "I'm feeling stressed", "how was my week", "show my mood chart"
  Calm         "breathing exercise", "calm me down", "play music", "stop music"
  Reminders    "remind me to call mom at 5:30 pm", "remind me in 10 minutes to stretch",
               "show my reminders", "cancel my reminders"
  Focus        "start focus for 45 minutes", "stop focus"
  To-do list   "add milk to my list", "what's on my list", "remove number 2",
               "remove milk", "clear my list"
  Notes        "write buy a new charger", "read my notes"
  Gratitude    "I'm grateful for my sister", "read my gratitude"
  Apps         "open youtube lo-fi beats", "open word", "open file notes.txt"
  Fun          "motivate me", "fortune", "fun mode", "tell me a joke"
  Info         "what time is it", "what's the date", "help"
  Session      "go to sleep" (wait for the wake word), "stop" (quit)
"""

_SWAPS = {
    "i am": "you are", "i'm": "you're", "im": "you're", "i've": "you've", "i'll": "you'll",
    "i'd": "you'd", "i": "you", "me": "you", "my": "your", "mine": "yours", "myself": "yourself",
}
_SWAP_RE = re.compile(r"(?<![\w'])(" + "|".join(sorted(map(re.escape, _SWAPS), key=len, reverse=True)) + r")(?![\w'])",
                      re.IGNORECASE)


def second_person(text: str) -> str:
    """'my sister and me' -> 'your sister and you'."""
    return _SWAP_RE.sub(lambda m: _SWAPS[m.group(1).lower()], text)


def part_of_day(now: datetime) -> str:
    if 5 <= now.hour < 12:
        return "morning"
    if 12 <= now.hour < 18:
        return "afternoon"
    return "evening"


def greeting(name: str, now: datetime) -> str:
    who = f", {name}" if name else ""
    return f"Good {part_of_day(now)}{who}!"


def joke(rng: random.Random | None = None) -> str:
    try:
        import pyjokes
        return pyjokes.get_joke()
    except Exception:
        return (rng or random).choice(FALLBACK_JOKES)
