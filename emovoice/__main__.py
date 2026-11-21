"""Command line entry point.

    python -m emovoice              # GUI (default)
    python -m emovoice --text       # terminal chat, no microphone or internet needed
    python -m emovoice --voice      # microphone + speech in the terminal
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import DATA_DIR, Settings, load_settings, migrate_legacy


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="emovoice", description="EmoVoice, an emotion-aware voice assistant.")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--text", action="store_true", help="chat in the terminal by typing")
    mode.add_argument("--voice", action="store_true", help="use the microphone and speech in the terminal")
    p.add_argument("--name", help="your name, used in greetings (overrides settings.json)")
    p.add_argument("--settings", type=Path, help="path to a settings.json file")
    p.add_argument("--data-dir", type=Path, help="where to keep moods, notes, reminders (default: data/)")
    p.add_argument("--no-mic", action="store_true", help="GUI only: typed input, no microphone")
    p.add_argument("--mute", action="store_true", help="GUI/voice: don't speak replies aloud")
    return p


def build_settings(args: argparse.Namespace) -> Settings:
    settings = load_settings(args.settings)
    if args.name:
        settings.user_name = args.name.strip()
    return settings


def _setup_logging(data_dir: Path) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    handlers[0].setLevel(logging.WARNING)
    try:
        file_handler = logging.FileHandler(data_dir / "emovoice.log", encoding="utf-8")
        file_handler.setLevel(logging.INFO)
        handlers.append(file_handler)
    except OSError:
        pass
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _print_event(item) -> None:
    kind, data = item
    if kind == "say":
        print(f"EmoVoice: {data['text']}")
    elif kind == "user":
        print(f"You: {data['text']}")
    elif kind == "status" and data.get("text"):
        print(f"  [{data['text']}]")
    elif kind == "help":
        print(data["text"])
    elif kind == "image":
        print(f"  [{data.get('title', 'image')} saved to {data['path']}]")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:  # emoji and accents must not crash a cp1252 console or a pipe
            stream.reconfigure(errors="replace", **({} if stream.isatty() else {"encoding": "utf-8"}))
        except (AttributeError, ValueError):
            pass

    args = build_parser().parse_args(argv)
    settings = build_settings(args)
    data_dir = args.data_dir.resolve() if args.data_dir else DATA_DIR
    data_dir.mkdir(parents=True, exist_ok=True)
    _setup_logging(data_dir)
    if not args.data_dir:
        migrate_legacy()

    from .storage import Store
    store = Store(data_dir, settings.history_limit)

    if args.text or args.voice:
        from .assistant import Assistant
        if args.text:
            from .speech import TextIO
            io = TextIO()
        else:
            from .speech import VoiceIO
            io = VoiceIO(settings, on_event=_print_event, use_tts=not args.mute)
        try:
            Assistant(io, store, settings).run()
        except KeyboardInterrupt:
            pass
        return 0

    try:
        from .gui import run_gui
        run_gui(settings, store, use_mic=not args.no_mic, use_tts=not args.mute)
    except Exception as exc:  # no display, no Tk
        if exc.__class__.__name__ not in ("TclError", "ImportError", "ModuleNotFoundError"):
            raise
        print(f"The GUI could not start ({exc}). Try: python -m emovoice --text", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
