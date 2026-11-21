"""Mood statistics, the 7-day mood chart and motivational quote images.

matplotlib is forced onto the Agg backend (no GUI, safe from worker threads) and
each chart gets its own figure that is closed after saving.
"""

from __future__ import annotations

import textwrap
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

from .mood import VALENCE
from .storage import MOODS, MoodEntry

MOOD_COLORS = {"happy": "#f6c945", "neutral": "#8a90a6", "anxious": "#b18cf2", "sad": "#5b8def", "angry": "#ef5b5b"}


def last_7_days(entries: Iterable[MoodEntry], now: datetime) -> tuple[list[date], dict[str, list[int]]]:
    """Counts per mood for each of the last 7 days (oldest first, today last)."""
    days = [now.date() - timedelta(days=i) for i in range(6, -1, -1)]
    index = {d: i for i, d in enumerate(days)}
    counts = {m: [0] * 7 for m in MOODS}
    for e in entries:
        if e.when and e.when.date() in index and e.when <= now:
            counts[e.mood][index[e.when.date()]] += 1
    return days, counts


def mood_chart(entries: Iterable[MoodEntry], now: datetime, out_path: Path) -> Path:
    import matplotlib
    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    days, counts = last_7_days(entries, now)
    labels = [d.strftime("%a\n%d") for d in days]
    fig, ax = plt.subplots(figsize=(7, 3.6), dpi=100)
    try:
        fig.patch.set_facecolor("#1d2030")
        ax.set_facecolor("#1d2030")
        bottom = [0] * 7
        x = list(range(7))
        for mood in MOODS:
            values = counts[mood]
            if any(values):
                ax.bar(x, values, bottom=bottom, color=MOOD_COLORS[mood], label=mood.capitalize(), width=0.6)
                bottom = [b + v for b, v in zip(bottom, values)]
        ax.set_xticks(x, labels)
        ax.set_title("Mood over the last 7 days", color="#e6e8f0")
        ax.set_ylabel("Check-ins", color="#9aa0b8")
        ax.tick_params(colors="#9aa0b8")
        ax.yaxis.get_major_locator().set_params(integer=True)
        for spine in ax.spines.values():
            spine.set_color("#3a3f58")
        if any(bottom):
            ax.set_ylim(0, max(bottom) * 1.15 + 0.6)  # headroom so the legend never covers a bar
            ax.legend(loc="upper left", fontsize=8, frameon=False, labelcolor="#e6e8f0", ncol=5)
        else:
            ax.text(0.5, 0.5, "No check-ins this week yet", transform=ax.transAxes,
                    ha="center", va="center", color="#9aa0b8")
        fig.tight_layout()
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out_path, facecolor=fig.get_facecolor())
    finally:
        plt.close(fig)
    return out_path


def _avg_valence(entries: list[MoodEntry]) -> float | None:
    return sum(VALENCE[e.mood] for e in entries) / len(entries) if entries else None


def mood_summary(entries: Iterable[MoodEntry], now: datetime) -> dict:
    """Stats for the last 7 days compared with the 7 days before."""
    entries = [e for e in entries if e.when and e.when <= now]
    week_start = datetime.combine(now.date() - timedelta(days=6), datetime.min.time())
    prev_start = week_start - timedelta(days=7)
    this_week = [e for e in entries if e.when >= week_start]
    last_week = [e for e in entries if prev_start <= e.when < week_start]

    dominant = None
    if this_week:
        counts = Counter(e.mood for e in this_week)
        top = max(counts.values())
        # prefer the most recent mood when counts tie
        dominant = next(e.mood for e in reversed(this_week) if counts[e.mood] == top)

    happiest_day = None
    by_day: dict[date, list[MoodEntry]] = {}
    for e in this_week:
        by_day.setdefault(e.when.date(), []).append(e)
    if len(by_day) >= 2:
        happiest_day = max(by_day, key=lambda d: (_avg_valence(by_day[d]), len(by_day[d]), d))

    this_avg, last_avg = _avg_valence(this_week), _avg_valence(last_week)
    trend = None
    if this_avg is not None and last_avg is not None:
        diff = this_avg - last_avg
        trend = "up" if diff > 0.15 else "down" if diff < -0.15 else "steady"

    return {"count": len(this_week), "previous_count": len(last_week), "dominant": dominant,
            "happiest_day": happiest_day, "trend": trend, "average": this_avg, "previous_average": last_avg}


def summary_text(summary: dict, now: datetime) -> str:
    if not summary["count"]:
        return "I don't have any mood check-ins from this week yet. Tell me how you're feeling any time."
    n = summary["count"]
    parts = [f"This week you checked in {n} time{'s' if n != 1 else ''}.",
             f"Your most common mood was {summary['dominant']}."]
    day = summary["happiest_day"]
    if day:
        name = "today" if day == now.date() else "yesterday" if day == now.date() - timedelta(days=1) else day.strftime("%A")
        parts.append(f"Your happiest day was {name}.")
    trend = summary["trend"]
    if trend == "up":
        parts.append("Compared with last week, your mood is trending up. Nice!")
    elif trend == "down":
        parts.append("Compared with last week, things have felt a bit heavier. Be gentle with yourself.")
    elif trend == "steady":
        parts.append("That's about the same as last week.")
    else:
        parts.append("I don't have enough from last week to compare yet.")
    return " ".join(parts)


def _font(size: int):
    from PIL import ImageFont
    for name in ("arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans.ttf",
                 "Helvetica.ttc", "/System/Library/Fonts/Helvetica.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def quote_image(quote: str, out_path: Path, background: Path | None = None, size=(800, 450)) -> Path:
    """Render quote onto background (cover-cropped and darkened) or a plain gradient."""
    from PIL import Image, ImageDraw, ImageOps

    width, height = size
    if background and Path(background).is_file():
        img = ImageOps.fit(Image.open(background).convert("RGB"), size)
        img = Image.blend(img, Image.new("RGB", size, (10, 10, 20)), 0.45)
    else:
        img = Image.new("RGB", size, (73, 109, 137))
        draw = ImageDraw.Draw(img)
        for y in range(height):
            t = y / height
            draw.line([(0, y), (width, y)], fill=(int(60 + 40 * t), int(70 + 20 * t), int(140 - 40 * t)))

    draw = ImageDraw.Draw(img)
    font = _font(44)
    lines = textwrap.wrap(quote, width=26) or [quote]
    boxes = [draw.textbbox((0, 0), line, font=font) for line in lines]
    line_h = max(b[3] - b[1] for b in boxes) + 14
    y = (height - line_h * len(lines)) // 2
    for line, box in zip(lines, boxes):
        x = (width - (box[2] - box[0])) // 2
        draw.text((x + 3, y + 3), line, font=font, fill=(0, 0, 0))
        draw.text((x, y), line, font=font, fill=(255, 230, 90), stroke_width=3, stroke_fill=(20, 20, 30))
        y += line_h

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    return out_path
