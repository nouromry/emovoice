"""Turn an utterance into an Intent using ordered, anchored rules.

Rules are tried top to bottom and the first match wins, so more specific
commands (reminders, "stop the music") sit above greedy ones (notes, to-dos).
Every keyword is matched as a whole word, so "address" never means "add" and
"remove" never contains the wake word "emo".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Iterable

from .timeparse import words_to_numbers


@dataclass(frozen=True)
class Intent:
    name: str
    args: dict = field(default_factory=dict)


def normalize(text: str) -> str:
    s = text.lower().replace("’", "'")
    s = re.sub(r"[,!?;\"]", " ", s)
    return " ".join(s.split()).strip(" .")


def contains_phrase(text: str, phrase: str) -> bool:
    """Whole-word, case-insensitive phrase search."""
    words = r"\s+".join(re.escape(w) for w in phrase.lower().split())
    return bool(words) and re.search(rf"(?<![\w']){words}(?![\w'])", normalize(text)) is not None


def is_exact_command(text: str, commands: Iterable[str]) -> bool:
    """True only when the whole utterance is one of commands ("stop", not "stop the music")."""
    said = re.sub(r"^(?:please\s+)|(?:\s+please|\s+now)$", "", normalize(text))
    return said in {normalize(c) for c in commands}


_WAKE_FILLERS = r"(?:(?:hey|hi|hello|ok|okay|yo|oh)\s+)?"


def _wake_regex(wake_words: Iterable[str]) -> re.Pattern:
    words = sorted({normalize(w) for w in wake_words if normalize(w)}, key=len, reverse=True)
    alternatives = "|".join(r"\s+".join(re.escape(p) for p in w.split()) for w in words) or r"(?!x)x"
    return re.compile(rf"(?<![\w']){_WAKE_FILLERS}(?:{alternatives})(?![\w'])")


def split_wake_word(text: str, wake_words: Iterable[str]) -> tuple[bool, str]:
    """Find the wake word as a whole word; return (found, the rest of the utterance).

    "emo, add milk to my list" -> (True, "add milk to my list")
    "remove the memo"          -> (False, "remove the memo")
    """
    s = normalize(text)
    m = _wake_regex(wake_words).search(s)
    if not m:
        return False, s
    if m.start() == 0:
        rest = s[m.end():]
    else:
        rest = s[:m.start()] + " " + s[m.end():]
    return True, " ".join(rest.split()).strip(" .:")


def strip_leading_wake_word(text: str, wake_words: Iterable[str]) -> str:
    s = normalize(text)
    m = _wake_regex(wake_words).match(s)
    return " ".join(s[m.end():].split()).strip(" .:") if m else s


_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
             "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10, "last": -1}


def parse_index(item: str) -> int | None:
    """'2', 'number 2', 'item two', 'the second one', 'the last item' -> list position."""
    s = re.sub(r"^the\s+", "", normalize(item))
    s = re.sub(r"\s+(?:one|item|thing|task)$", "", s)  # before words_to_numbers turns "one" into 1
    if s in _ORDINALS:
        return _ORDINALS[s]
    m = re.fullmatch(r"(?:number|item|task|no\.?|#)?\s*(\d+)(?:st|nd|rd|th)?", words_to_numbers(s))
    return int(m.group(1)) if m else None


# --------------------------------------------------------------------------- rules

_LIST = r"(?:(?:my|the)\s+)?(?:(?:to\s?-?\s?do|todo|shopping|task)\s+)?list"
_Rule = tuple[str, re.Pattern, Callable[[re.Match], dict]]


def _groups(*names: str) -> Callable[[re.Match], dict]:
    def extract(m: re.Match) -> dict:
        out = {}
        for name in names:
            key = name.rstrip("0123456789")
            value = m.groupdict().get(name)
            if value is not None and not out.get(key):
                out[key] = value.strip()
            out.setdefault(key, "")
        return out
    return extract


def _no_args(m: re.Match) -> dict:
    return {}


def _rule(name: str, pattern: str, extract: Callable[[re.Match], dict] = _no_args) -> _Rule:
    return name, re.compile(pattern), extract


RULES: list[_Rule] = [
    _rule("help", r"^(?:help|what\s+can\s+you\s+do|commands|show\s+(?:me\s+)?(?:the\s+)?(?:help|commands)|list\s+commands|how\s+do\s+i\s+use\s+you)$"),
    _rule("greeting", r"^(?:hi|hello|hey|hiya|good\s+(?:morning|afternoon|evening))(?:\s+there)?$"),
    _rule("sleep", r"^(?:go\s+to\s+sleep|sleep|sleep\s+mode|go\s+to\s+sleep\s+now|take\s+a\s+nap|stand\s?by)$"),
    _rule("stop_music", r"^(?:please\s+)?(?:stop|pause|end|turn\s+off|kill)\s+(?:the\s+|that\s+)?(?:music|song|playback|playing|sound)$"),
    _rule("stop_focus", r"^(?:please\s+)?(?:stop|end|cancel|finish)\s+(?:the\s+|my\s+)?(?:focus|pomodoro)(?:\s+(?:session|timer|mode))?$"),
    _rule("start_focus",
          r"^(?:please\s+)?(?:start|begin|set)\s+(?:a\s+)?(?:focus|pomodoro)(?:\s+(?:session|timer|mode))?(?:\s+for\s+(?P<duration>.+))?$"
          r"|^focus\s+(?:for\s+)?(?P<duration2>.+)$",
          _groups("duration", "duration2")),
    _rule("show_reminders",
          r"\b(?:show|list|read|tell)\s+(?:me\s+)?(?:all\s+)?(?:of\s+)?my\s+reminders\b"
          r"|\bwhat\s+(?:are\s+my\s+reminders|reminders\s+do\s+i\s+have)\b|^(?:my\s+)?reminders$"),
    _rule("cancel_reminders",
          r"\b(?:cancel|clear|delete|remove)\s+(?:all\s+)?(?:of\s+)?(?:my|the)\s+reminders\b|^cancel\s+(?:all\s+)?reminders$"),
    _rule("remind", r"(?P<text>.*\b(?:remind\s+me|set\s+(?:a\s+|an\s+)?reminder|add\s+(?:a\s+)?reminder)\b.*|^reminder\b.*)",
          _groups("text")),
    _rule("breathing",
          r"\b(?:breathing\s+exercise|breathe\s+with\s+me|calm\s+me\s+down|help\s+me\s+(?:calm\s+down|relax|breathe)"
          r"|box\s+breathing|guided\s+breathing|let's\s+breathe)\b"),
    _rule("gratitude_add",
          r"^(?:i\s+am|i'm|im|i\s+feel)\s+(?:so\s+|really\s+|very\s+|truly\s+)?(?:grateful|thankful)\s+(?:for|that)\s+(?P<what>.+)$",
          _groups("what")),
    _rule("gratitude_read",
          r"\b(?:read|show|tell)\s+(?:me\s+)?my\s+gratitude(?:\s+(?:journal|list|entries|notes))?\b|^gratitude(?:\s+journal)?$"),
    _rule("weekly_recap",
          r"\bhow\s+(?:was|has\s+been|is)\s+my\s+week\b|\b(?:weekly|week's|week)\s+(?:mood\s+)?(?:recap|summary|report)\b|\bmood\s+(?:recap|summary)\b"),
    _rule("mood_chart",
          r"\b(?:show|display|open|draw)\s+(?:me\s+)?(?:my\s+|the\s+)?mood\s+(?:chart|graph|tracker|history)\b|^mood\s+chart$"),
    _rule("time", r"\bwhat(?:'s|\s+is)\s+the\s+time\b|\bwhat\s+time\s+is\s+it\b|\btell\s+me\s+the\s+time\b|^(?:the\s+)?time(?:\s+please)?$"),
    _rule("date", r"\bwhat(?:'s|\s+is)\s+(?:the\s+|today's\s+)?date\b|\bwhat\s+day\s+is\s+(?:it|today)\b|^what's\s+today$|^(?:the\s+|today's\s+)?date$"),
    _rule("read_notes", r"\b(?:read|show|tell)\s+(?:me\s+)?(?:my|the)\s+notes\b|^(?:my\s+)?notes$"),
    _rule("write_note",
          r"^(?:please\s+)?(?:write(?:\s+down)?|take\s+(?:a\s+)?note(?:\s+of)?|make\s+a\s+note(?:\s+of)?|note(?:\s+down)?)"
          r"(?:\s+(?:that|this))?(?![\w'])[\s:]*(?P<text>.*)$",
          _groups("text")),
    _rule("play_music",
          r"^(?:please\s+)?(?:play|start|put\s+on)\s+(?:some\s+|the\s+|my\s+)?(?:calm(?:ing)?\s+|relaxing\s+|soothing\s+)?music$"),
    _rule("youtube",
          r"^(?:open|play|search)\s+youtube(?:\s+(?:and\s+)?(?:play\s+|search\s+(?:for\s+)?)?(?P<query>.*))?$"
          r"|^(?:play|search\s+(?:for\s+)?|find|watch)\s+(?P<query2>.+?)\s+on\s+youtube$",
          _groups("query", "query2")),
    _rule("open_word", r"^(?:please\s+)?(?:open|launch|start|run)\s+(?:microsoft\s+|ms\s+)?word(?:\s+processor)?$"),
    _rule("open_file", r"^(?:please\s+)?open\s+(?:a\s+|the\s+|my\s+)?file(?:\s+(?:called\s+|named\s+)?(?P<name>.+))?$",
          _groups("name")),
    _rule("todo_clear", rf"^(?:please\s+)?(?:clear|empty|wipe|erase|delete)\s+(?:out\s+)?(?:all\s+(?:of\s+)?)?{_LIST}$"),
    _rule("todo_show",
          rf"\bwhat(?:'s|s|\s+is)\s+on\s+{_LIST}\b|^(?:show|read|list|open|tell)\s+(?:me\s+)?{_LIST}$|^(?:my\s+)?(?:to\s?-?\s?do|todo)\s+list$|^show\s+list$"),
    _rule("todo_add",
          rf"^(?:please\s+)?(?:add|put)(?:\s+(?P<item>.+?))?(?:\s+(?:to|on|onto)\s+{_LIST})?$",
          _groups("item")),
    _rule("todo_remove",
          rf"^(?:please\s+)?(?:remove|delete|cross\s+off|tick\s+off|scratch)(?:\s+(?P<item>.+?))?(?:\s+(?:from|off)\s+{_LIST})?$",
          _groups("item")),
    _rule("motivate",
          r"\b(?:motivate\s+me|motivation(?:al)?(?:\s+quote)?|inspire\s+me|give\s+me\s+(?:a\s+)?quote|quote\s+of\s+the\s+day)\b|^motivate$"),
    _rule("fortune", r"\b(?:fortune|tell\s+my\s+fortune)\b"),
    _rule("fun_mode", r"\bfun\s+mode\b"),
    _rule("joke", r"\b(?:tell\s+me\s+a\s+joke|make\s+me\s+laugh|joke)\b"),
]

INTENT_NAMES = [name for name, _, _ in RULES]


def parse_intent(text: str) -> Intent | None:
    s = normalize(text)
    if not s:
        return None
    for name, pattern, extract in RULES:
        m = pattern.search(s)
        if m:
            return Intent(name, extract(m))
    return None
