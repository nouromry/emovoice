"""Emotion detection that works on speech-to-text output (no punctuation needed).

A small emotion lexicon gives each word a mood and weight. Negation ("not happy")
flips or cancels it, intensifiers ("so", "really") boost it, and TextBlob's
polarity is blended in when available. Moods: happy, sad, angry, anxious, neutral.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_LEXICON: dict[str, tuple[str, float]] = {}


def _add(mood: str, weight: float, words: str) -> None:
    for w in words.split():
        _LEXICON[w] = (mood, weight)


_add("happy", 1.0, "happy glad awesome amazing wonderful fantastic excited joyful cheerful delighted "
                   "thrilled ecstatic overjoyed elated yay brilliant")
_add("happy", 0.8, "great proud grateful thankful blessed optimistic lovely")
_add("happy", 0.6, "good love loving content relaxed peaceful hopeful smiling laughing best better nice fun")
_add("sad", 1.0, "sad unhappy depressed miserable lonely heartbroken hopeless crying cry cried "
                 "worthless grieving grief devastated")
_add("sad", 0.8, "upset gloomy disappointed hurt empty awful terrible")
_add("sad", 0.6, "down low lost alone tired exhausted bad sorry miss")
_add("angry", 1.0, "angry mad furious frustrated hate rage livid pissed outraged fuming enraged")
_add("angry", 0.8, "annoyed irritated frustrating annoying resent infuriating")
_add("anxious", 1.0, "anxious anxiety stressed nervous worried panic panicking panicked overwhelmed "
                     "terrified scared afraid frightened")
_add("anxious", 0.8, "stress stressful worry worrying tense uneasy fear dread")
_add("anxious", 0.5, "restless pressure")

_PHRASES = [
    (re.compile(r"\bfed\s+up\b"), "angry", 1.0),
    (re.compile(r"\bsick\s+(?:and\s+tired\s+)?of\b"), "angry", 0.8),
    (re.compile(r"\bpissed\s+off\b"), "angry", 0.4),
    (re.compile(r"\bstressed\s+out\b"), "anxious", 0.3),
    (re.compile(r"\bfreaking\s+out\b"), "anxious", 1.2),
    (re.compile(r"\bon\s+edge\b"), "anxious", 1.0),
    (re.compile(r"\bburn(?:ed|t)\s+out\b"), "anxious", 0.9),
    (re.compile(r"\bover\s+the\s+moon\b"), "happy", 1.2),
    (re.compile(r"\bon\s+cloud\s+nine\b"), "happy", 1.2),
    (re.compile(r"\bfeel(?:ing)?\s+(?:so\s+|really\s+)?down\b"), "sad", 0.5),
]
_NEGATORS = {"not", "no", "never", "dont", "don't", "isnt", "isn't", "wasnt", "wasn't", "aren't",
             "arent", "cant", "can't", "cannot", "hardly", "barely", "n't", "nothing", "neither", "nor"}
_INTENSIFIERS = {"very", "really", "so", "extremely", "super", "totally", "incredibly", "absolutely",
                 "too", "deeply", "utterly", "completely", "truly", "insanely", "terribly", "awfully"}
_DIMINISHERS = {"bit", "slightly", "little", "somewhat", "kinda", "kind", "sort", "mildly", "fairly"}
_NEUTRALS = {"okay", "ok", "fine", "alright", "meh", "so-so"}
_CLAUSE_BREAKS = {"but", "though", "although", "however", "yet"}

LOW_MOODS = frozenset({"sad", "angry", "anxious"})
VALENCE = {"happy": 1.0, "neutral": 0.0, "anxious": -0.6, "sad": -1.0, "angry": -0.8}
THRESHOLD = 0.45
POLARITY_WEIGHT = 0.8


@dataclass(frozen=True)
class MoodResult:
    mood: str
    score: float        # valence, -1 (very negative) .. +1 (very positive)
    confidence: float   # 0 .. 1

    @property
    def is_low(self) -> bool:
        return self.mood in LOW_MOODS


_polarity_fn = None


def _polarity(text: str) -> float:
    """TextBlob polarity, or 0.0 if TextBlob is not installed."""
    global _polarity_fn
    if _polarity_fn is None:
        try:
            from textblob import TextBlob
            _polarity_fn = lambda t: float(TextBlob(t).sentiment.polarity)  # noqa: E731
        except Exception:  # pragma: no cover - optional dependency
            _polarity_fn = lambda t: 0.0  # noqa: E731
    try:
        return _polarity_fn(text)
    except Exception:  # pragma: no cover - corpora missing etc.
        return 0.0


def _tokens(text: str) -> list[str]:
    s = text.lower().replace("’", "'")
    s = re.sub(r"(\w)n't\b", r"\1 n't", s)
    return re.findall(r"n't|[a-z]+(?:'[a-z]+)?", s)


def detect_mood(text: str, use_polarity: bool = True) -> MoodResult:
    scores = {"happy": 0.0, "sad": 0.0, "angry": 0.0, "anxious": 0.0}
    lowered = text.lower()
    for pattern, mood, weight in _PHRASES:
        if pattern.search(lowered):
            scores[mood] += weight

    tokens = _tokens(text)
    for i, tok in enumerate(tokens):
        if tok not in _LEXICON:
            continue
        mood, weight = _LEXICON[tok]
        window: list[str] = []
        for prev in reversed(tokens[max(0, i - 3):i]):
            if prev in _CLAUSE_BREAKS or prev in _LEXICON:
                break
            window.append(prev)
        for w in window:
            if w in _INTENSIFIERS:
                weight *= 1.5
            elif w in _DIMINISHERS:
                weight *= 0.6
        if any(w in _NEGATORS for w in window):
            if mood == "happy":
                scores["sad"] += weight * 0.9          # "not happy" -> sad
            else:
                scores["happy"] += weight * 0.2        # "not sad" is mildly positive, never sad
        else:
            scores[mood] += weight

    polarity = _polarity(text) if use_polarity else 0.0
    # TextBlob misses unpunctuated negation ("i dont feel good" scores +0.7), so only
    # trust positive polarity when the sentence has no negator or neutral word.
    if polarity > 0.1 and not (_NEUTRALS | _NEGATORS).intersection(tokens):
        scores["happy"] += polarity * POLARITY_WEIGHT
    elif polarity < -0.1:
        negatives = {m: scores[m] for m in ("sad", "angry", "anxious")}
        target = max(negatives, key=negatives.get) if any(negatives.values()) else "sad"
        scores[target] += -polarity * POLARITY_WEIGHT

    best = max(scores, key=scores.get)
    total = sum(scores.values())
    valence = sum(VALENCE[m] * s for m, s in scores.items()) / total if total else 0.0
    valence = max(-1.0, min(1.0, valence))
    if scores[best] < THRESHOLD:
        return MoodResult("neutral", round(valence, 3), round(1.0 - scores[best] / THRESHOLD * 0.5, 3))
    confidence = min(1.0, scores[best] / (total + 0.5) + 0.2)
    return MoodResult(best, round(valence, 3), round(confidence, 3))


_FEELING = re.compile(
    r"\b(?:i\s+(?:feel|felt|am|was|'m)|i'm|im|feeling|my\s+mood|today\s+(?:is|was)|it's\s+been|i've\s+been)\b"
)


def is_feeling_statement(text: str) -> bool:
    """Heuristic: is the user talking about how they feel (worth logging even if neutral)?"""
    return bool(_FEELING.search(text.lower().replace("’", "'")))
