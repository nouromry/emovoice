"""Tkinter front end.

Threading rule: background threads (assistant, reminders, focus timer, TTS)
never touch a widget. They put (kind, data) tuples on self.events and the main
thread drains that queue with root.after(). SessionController holds the thread
logic and has no Tk dependency, so it can be tested without a display.
"""

from __future__ import annotations

import logging
import queue
import sys
import threading
from pathlib import Path
from typing import Callable

from . import content, visuals
from .assistant import MOOD_EMOJI, Assistant
from .config import Settings
from .speech import VoiceIO
from .storage import Store
from .system import MusicPlayer, System

log = logging.getLogger(__name__)

BG, PANEL, PANEL2, BORDER = "#14161f", "#1d2030", "#262a3d", "#3a3f58"
TEXT, MUTED, ACCENT, ACCENT2, DANGER = "#e6e8f0", "#9aa0b8", "#7c6cff", "#4fd1c5", "#ef5b5b"
MOOD_COLORS = dict(visuals.MOOD_COLORS)
FONT = "Segoe UI" if sys.platform.startswith("win") else "Helvetica Neue" if sys.platform == "darwin" else "DejaVu Sans"
STATUS_TEXT = {
    "sleeping": "😴  Waiting for 'Emo' (typing always works)",
    "listening": "🎙️  Listening…",
    "breathing": "🫁  Breathing together…",
    "stopped": "⏹  Stopped. Press Start",
}


class SessionController:
    """Starts at most one assistant thread at a time."""

    def __init__(self, make_assistant: Callable[[], Assistant], events: queue.Queue):
        self._make = make_assistant
        self.events = events
        self.assistant: Assistant | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        with self._lock:
            if self.running:
                return False
            self.assistant = self._make()
            self._thread = threading.Thread(target=self._run, args=(self.assistant,),
                                            name="emovoice-assistant", daemon=True)
            self._thread.start()
            self.events.put(("session_started", {}))
            return True

    def stop(self) -> None:
        if self.assistant is not None:
            self.assistant.stop()

    def _run(self, assistant: Assistant) -> None:
        try:
            assistant.run()
        except Exception:
            log.exception("Assistant crashed")
            self.events.put(("say", {"text": "Something went wrong and the session stopped."}))
        finally:
            self.events.put(("session_ended", {}))


class EmoVoiceApp:
    def __init__(self, root, settings: Settings, store: Store, *, io: VoiceIO | None = None,
                 use_mic: bool = True, use_tts: bool = True, poll_ms: int = 80):
        import tkinter as tk

        self.tk = tk
        self.root = root
        self.settings = settings
        self.store = store
        self.events: queue.Queue = queue.Queue()
        self.system = System()
        self.player = MusicPlayer()
        self.io = io or VoiceIO(settings, on_event=self.events.put, use_mic=use_mic,
                                use_tts=use_tts, shows_images=True)
        self.controller = SessionController(
            lambda: Assistant(self.io, store, settings, system=self.system, player=self.player), self.events)
        self._photo = None
        self._image_path: Path | None = None
        self._toasts: list = []
        self.poll_ms = poll_ms
        self.scale = max(1.0, root.winfo_fpixels("1i") / 96)  # >1 on high-DPI Windows displays
        self._build()
        self._show_system("Press Start (or just type) to begin. Say 'help' to see what I can do.")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(self.poll_ms, self._poll)

    # ------------------------------------------------------------------ layout
    def _button(self, parent, text, command, bg=ACCENT):
        tk = self.tk
        return tk.Button(parent, text=text, command=command, bg=bg, fg="white", activebackground=BORDER,
                         activeforeground="white", disabledforeground="#6b7090", relief="flat", bd=0,
                         padx=14, pady=6, font=(FONT, 10, "bold"), cursor="hand2", highlightthickness=0)

    def _build(self) -> None:
        tk = self.tk
        root = self.root
        root.title("EmoVoice")
        root.configure(bg=BG)
        px = lambda n: int(n * self.scale)  # noqa: E731
        root.geometry(f"{px(1000)}x{px(660)}")
        root.minsize(px(760), px(520))

        header = tk.Frame(root, bg=PANEL, padx=16, pady=10)
        header.pack(fill="x")
        tk.Label(header, text="🎤 EmoVoice", bg=PANEL, fg=TEXT, font=(FONT, 16, "bold")).pack(side="left")
        self.status = tk.Label(header, text=STATUS_TEXT["stopped"], bg=PANEL, fg=MUTED, font=(FONT, 10))
        self.status.pack(side="left", padx=16)
        self.mood_badge = tk.Label(header, text="😐 No mood yet", bg=MOOD_COLORS["neutral"], fg="#111",
                                   font=(FONT, 11, "bold"), padx=12, pady=4)
        self.mood_badge.pack(side="right")

        body = tk.Frame(root, bg=BG, padx=12, pady=12)
        body.pack(fill="both", expand=True)

        chat_frame = tk.Frame(body, bg=PANEL2, highlightbackground=BORDER, highlightthickness=1)
        chat_frame.pack(side="left", fill="both", expand=True)
        self.chat = tk.Text(chat_frame, wrap="word", bg=PANEL2, fg=TEXT, relief="flat", bd=0, padx=12, pady=10,
                            font=(FONT, 11), state="disabled", cursor="arrow", insertbackground=TEXT,
                            highlightthickness=0)
        from tkinter import ttk
        style = ttk.Style(root)
        style.theme_use("clam")  # the only built-in theme that honours scrollbar colours
        style.configure("Dark.Vertical.TScrollbar", background=BORDER, troughcolor=PANEL2, bordercolor=PANEL2,
                        arrowcolor=MUTED, lightcolor=BORDER, darkcolor=BORDER)
        scroll = ttk.Scrollbar(chat_frame, command=self.chat.yview, style="Dark.Vertical.TScrollbar")
        self.chat.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.chat.pack(side="left", fill="both", expand=True)
        self.chat.tag_configure("who_bot", foreground=ACCENT, font=(FONT, 10, "bold"))
        self.chat.tag_configure("who_user", foreground=ACCENT2, font=(FONT, 10, "bold"))
        self.chat.tag_configure("bot", foreground=TEXT, spacing3=8)
        self.chat.tag_configure("user", foreground=TEXT, spacing3=8)
        self.chat.tag_configure("system", foreground=MUTED, font=(FONT, 10, "italic"), spacing3=8)
        self.chat.tag_configure("mono", foreground=MUTED, font=("Consolas" if FONT == "Segoe UI" else "Courier", 9),
                                spacing3=8)

        side = tk.Frame(body, bg=PANEL, width=px(380), padx=12, pady=12, highlightbackground=BORDER,
                        highlightthickness=1)
        side.pack(side="right", fill="y", padx=(12, 0))
        side.pack_propagate(False)
        tk.Label(side, text="Focus timer", bg=PANEL, fg=MUTED, font=(FONT, 9)).pack(anchor="w")
        self.focus_label = tk.Label(side, text="--:--", bg=PANEL, fg=TEXT, font=(FONT, 26, "bold"))
        self.focus_label.pack(anchor="w", pady=(0, 12))
        tk.Label(side, text="Charts and quotes", bg=PANEL, fg=MUTED, font=(FONT, 9)).pack(anchor="w")
        self.image_label = tk.Label(side, bg=PANEL2, fg=MUTED, text="Say 'show my mood chart'\nor 'motivate me'",
                                    font=(FONT, 10), height=12, cursor="hand2")
        self.image_label.pack(fill="x", pady=(4, 4))
        self.image_label.bind("<Button-1>", self._open_image_externally)
        self.image_caption = tk.Label(side, text="", bg=PANEL, fg=MUTED, font=(FONT, 9))
        self.image_caption.pack(anchor="w")

        bottom = tk.Frame(root, bg=BG, padx=12)
        bottom.pack(fill="x", pady=(0, 12))
        entry_row = tk.Frame(bottom, bg=BG)
        entry_row.pack(fill="x")
        self.entry = tk.Entry(entry_row, bg=PANEL2, fg=TEXT, insertbackground=TEXT, relief="flat",
                              font=(FONT, 12), highlightthickness=1, highlightbackground=BORDER,
                              highlightcolor=ACCENT)
        self.entry.pack(side="left", fill="x", expand=True, ipady=7)
        self.entry.bind("<Return>", lambda e: self.send())
        self._button(entry_row, "Send", self.send).pack(side="left", padx=(8, 0))

        buttons = tk.Frame(bottom, bg=BG, pady=8)
        buttons.pack(fill="x")
        self.start_button = self._button(buttons, "▶ Start", self.start)
        self.start_button.pack(side="left")
        self.stop_button = self._button(buttons, "■ Stop", self.stop, bg=DANGER)
        self.stop_button.pack(side="left", padx=8)
        self.stop_button.configure(state="disabled")
        self._button(buttons, "Help", self.show_help, bg=BORDER).pack(side="left")
        self._button(buttons, "Mood chart", self.show_mood_chart, bg=BORDER).pack(side="left", padx=8)
        self.entry.focus_set()

    # ------------------------------------------------------------------ actions (main thread)
    def start(self) -> bool:
        started = self.controller.start()
        if started:
            self.start_button.configure(state="disabled")
            self.stop_button.configure(state="normal")
        return started

    def stop(self) -> None:
        self.controller.stop()
        self.stop_button.configure(state="disabled")

    def send(self) -> None:
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")
        if not self.controller.running:
            self.start()
        self.io.submit(text)

    def show_help(self) -> None:
        if self.controller.running:
            self.io.submit("help")
        else:
            self._append_block(content.HELP_TEXT)

    def show_mood_chart(self) -> None:
        if self.controller.running:
            self.io.submit("show my mood chart")
            return

        def work():  # matplotlib runs on Agg in a worker; the result comes back as an event
            try:
                from datetime import datetime
                path = visuals.mood_chart(self.store.moods(), datetime.now(), self.store.data_dir / "mood_chart.png")
                self.events.put(("image", {"path": str(path), "title": "Mood chart"}))
            except Exception as exc:
                log.exception("Mood chart failed")
                self.events.put(("say", {"text": f"I couldn't draw the chart: {exc}"}))
        threading.Thread(target=work, daemon=True).start()

    def close(self) -> None:
        self.controller.stop()
        self.player.stop()
        self.root.destroy()

    # ------------------------------------------------------------------ event pump (main thread)
    def _poll(self) -> None:
        self.process_events()
        try:
            self.root.after(self.poll_ms, self._poll)
        except self.tk.TclError:
            pass  # window closed

    def process_events(self, limit: int = 100) -> int:
        handled = 0
        while handled < limit:
            try:
                kind, data = self.events.get_nowait()
            except queue.Empty:
                break
            handled += 1
            try:
                getattr(self, f"_on_{kind}", self._on_unknown)(**data)
            except Exception:
                log.exception("UI event %s failed", kind)
        return handled

    def _on_unknown(self, **data) -> None:
        pass

    def _on_say(self, text: str) -> None:
        self._append("EmoVoice", text, "bot")

    def _on_user(self, text: str) -> None:
        self._append("You", text, "user")

    def _on_help(self, text: str) -> None:
        self._append_block(text)

    def _on_status(self, state: str, text: str | None = None) -> None:
        self.status.configure(text=text or STATUS_TEXT.get(state, state))
        if text:
            self._show_system(text)

    def _on_mood(self, mood: str, emoji: str | None = None, confidence: float | None = None) -> None:
        self.mood_badge.configure(text=f"{emoji or MOOD_EMOJI.get(mood, '')} {mood.capitalize()}",
                                  bg=MOOD_COLORS.get(mood, MOOD_COLORS["neutral"]))

    def _on_focus(self, remaining: int, total: int, done: bool = False) -> None:
        if done:
            self.focus_label.configure(text="Done 🎉", fg=ACCENT2)
        elif remaining <= 0:
            self.focus_label.configure(text="--:--", fg=TEXT)
        else:
            self.focus_label.configure(text=f"{remaining // 60:02d}:{remaining % 60:02d}", fg=TEXT)

    def _on_reminder(self, message: str, due: str | None = None) -> None:
        self.toast(f"⏰ {message}")

    def _on_image(self, path: str, title: str = "") -> None:
        from PIL import Image, ImageTk
        with Image.open(path) as img:
            img = img.convert("RGB")
            self.image_label.update_idletasks()
            width = self.image_label.winfo_width() - 8
            img.thumbnail((width if width > 50 else int(340 * self.scale), int(300 * self.scale)))
            self._photo = ImageTk.PhotoImage(img)
        self._image_path = Path(path)
        self.image_label.configure(image=self._photo, text="", height=self._photo.height())
        self.image_caption.configure(text=f"{title}  (click to open full size)" if title else "")

    def _on_session_started(self) -> None:
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")

    def _on_session_ended(self) -> None:
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.status.configure(text=STATUS_TEXT["stopped"])

    def _on_stopped(self) -> None:
        pass

    # ------------------------------------------------------------------ widgets helpers
    def _append(self, who: str, text: str, tag: str) -> None:
        self.chat.configure(state="normal")
        self.chat.insert("end", f"{who}\n", f"who_{tag}")
        self.chat.insert("end", f"{text}\n", tag)
        self.chat.configure(state="disabled")
        self.chat.see("end")

    def _append_block(self, text: str) -> None:
        self.chat.configure(state="normal")
        self.chat.insert("end", text.rstrip() + "\n", "mono")
        self.chat.configure(state="disabled")
        self.chat.see("end")

    def _show_system(self, text: str) -> None:
        self.chat.configure(state="normal")
        self.chat.insert("end", text + "\n", "system")
        self.chat.configure(state="disabled")
        self.chat.see("end")

    def toast(self, message: str, ms: int = 10000):
        """A non-modal notification in the top-right corner."""
        tk = self.tk
        frame = tk.Frame(self.root, bg=ACCENT, padx=1, pady=1)
        inner = tk.Frame(frame, bg=PANEL2, padx=12, pady=8)
        inner.pack()
        tk.Label(inner, text=message, bg=PANEL2, fg=TEXT, font=(FONT, 11), wraplength=300,
                 justify="left").pack(side="left")
        tk.Button(inner, text="✕", command=lambda: self._dismiss(frame), bg=PANEL2, fg=MUTED, relief="flat",
                  bd=0, activebackground=PANEL2, cursor="hand2").pack(side="left", padx=(10, 0))
        self._toasts.append(frame)
        self._place_toasts()
        self.root.after(ms, lambda: self._dismiss(frame))
        self._show_system(message)
        return frame

    def _dismiss(self, frame) -> None:
        if frame in self._toasts:
            self._toasts.remove(frame)
            frame.destroy()
            self._place_toasts()

    def _place_toasts(self) -> None:
        y = 64
        for frame in self._toasts:
            frame.place(relx=1.0, x=-16, y=y, anchor="ne")
            frame.update_idletasks()
            y += frame.winfo_reqheight() + 8

    def _open_image_externally(self, _event=None) -> None:
        if self._image_path:
            self.system.open_path(self._image_path)


def enable_dpi_awareness() -> None:
    """Crisp rendering on scaled Windows displays (otherwise Tk is bitmap-stretched and blurry)."""
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass


def run_gui(settings: Settings, store: Store, use_mic: bool = True, use_tts: bool = True) -> None:
    import tkinter as tk
    enable_dpi_awareness()
    root = tk.Tk()
    EmoVoiceApp(root, settings, store, use_mic=use_mic, use_tts=use_tts)
    root.mainloop()
