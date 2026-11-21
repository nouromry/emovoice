# EmoVoice Assistant 🎤🤖

A desktop voice assistant that listens for your commands and pays attention to how you feel.
It runs on Windows, macOS and Linux. You can talk to it, or type when there is no microphone
or internet.

- **Wake word:** say "Emo", or say it in one breath: "Emo, add milk to my list". Typed messages
  don't need the wake word.
- **Mood detection:** recognises happy, sad, angry, anxious and neutral, including negation
  ("not happy") and intensifiers ("so angry").
- **Reacting to your mood:**
  - a joke when you're sad, plus a reminder of something you said you're grateful for;
  - guided box breathing when you're angry or anxious;
  - after three low moods in one day, a gentle suggestion to talk to someone you trust.
- **Organising your day:** reminders (saved across restarts), a to-do list, notes, a focus
  (Pomodoro) timer and a gratitude journal.
- **Looking back:** a 7-day mood chart and a weekly recap.
- **Interface:** a dark GUI with a chat log, a mood badge, a live focus countdown, charts shown
  in the window, and reminder pop-ups that don't block the window.

## Setup

```bash
python -m venv venv
venv\Scripts\activate            # Windows
source venv/bin/activate         # macOS / Linux

pip install -r requirements.txt
python -m textblob.download_corpora lite   # optional extra TextBlob data
pip install -e .                 # optional: lets `python -m emovoice` run from any folder
```

Platform notes:

- **Linux:** install Tkinter and PortAudio with `sudo apt install python3-tk portaudio19-dev`.
  "Open word" uses LibreOffice Writer.
- **macOS:** `brew install portaudio` before installing PyAudio.
- **Voice input** uses Google's speech service, so it needs internet. Typing works offline.
- **pygame** is recommended for music. Without it, EmoVoice tries `afplay`, `ffplay` or
  `mpg123`, and then `playsound`, which can't be stopped early.

## Running

```bash
python emovoice.py                 # GUI (default). Works from any working directory
python -m emovoice                 # same thing
python -m emovoice --text          # terminal chat: no microphone, speakers or internet needed
python -m emovoice --voice         # microphone + speech in the terminal
python -m emovoice --name Sam      # greet you by name
python -m emovoice --no-mic        # GUI with typed input only
python -m emovoice --mute          # don't speak replies aloud
python -m emovoice --data-dir PATH --settings PATH
```

In the GUI:

- Press **Start** to begin, or just type. Typing starts a session automatically.
- **Stop** ends the session.
- **Help** lists the commands.
- **Mood chart** draws your last 7 days.
- Click a chart or quote image to open it full size.

## Commands

| Say or type | What happens |
|---|---|
| "I'm feeling stressed" / "I'm so happy" | Mood check-in: logged, with a matching response (joke, breathing, encouragement) |
| "how was my week" | Spoken recap: check-ins, most common mood, happiest day, trend vs last week |
| "show my mood chart" | Stacked bar chart of the last 7 days |
| "breathing exercise" / "calm me down" | Guided box breathing (in 4, hold 4, out 4, hold 4) |
| "play music" / "stop music" | Calming music without blocking the assistant |
| "remind me to call mom at 5:30 pm" | Saved reminder. Also: "in 10 minutes", "in half an hour", "tomorrow at 9", "at noon", "tonight", "on Monday", or the time first: "at 5 pm remind me to…" |
| "show my reminders" / "cancel my reminders" | List or clear pending reminders |
| "start focus for 45 minutes" / "stop focus" | Focus timer with a live countdown in the GUI |
| "add milk to my list" | Add a to-do |
| "what's on my list" | Read the numbered list |
| "remove number 2" / "remove milk" / "remove the last one" | Remove by number, by name or by part of the name |
| "clear my list" | Empty the list (asks you to confirm) |
| "write buy a charger" / "read my notes" | Notes, one timestamped line each |
| "I'm grateful for my sister" / "read my gratitude" | Gratitude journal |
| "open youtube lo-fi beats" / "open youtube" | Play a video or open YouTube |
| "open word" | Microsoft Word, or LibreOffice / Pages / TextEdit |
| "open file" / "open file budget.xlsx" | Finds the file in data/, the project, Documents, Desktop or Downloads |
| "motivate me" | Quote image on `motivational_bg.jpg` |
| "fortune" / "fun mode" / "tell me a joke" | Just for fun |
| "what time is it" / "what's the date" / "hello" | Quick info and a greeting for the time of day |
| "help" | Command list |
| "go to sleep" | Waits for the wake word again |
| "stop" | Quits. Only the whole word: "stop the music" doesn't quit |

## What was fixed (v1 → v2)

| v1 problem | Fix |
|---|---|
| Pillow and matplotlib missing from requirements, plus a bogus `tk` entry | Correct `requirements.txt` with pygame recommended, and a new `requirements-dev.txt` |
| README said media lived in `assets/` but it was in the root | `find_asset()` checks `assets/`, `assets/*/`, then the project root |
| Windows only (`os.startfile`, `start winword`) | `system.py` handles Windows, macOS and Linux, including LibreOffice |
| Paths relative to the working directory | All paths come from the package location |
| Mood reply followed by "I didn't understand the command" | Exactly one handler runs per utterance |
| "I'm sad" never reached the mood handler | Anything that isn't a command goes to the mood handler |
| "Angry" needed a `!` that speech-to-text never produces | Emotion lexicon with negation and intensifiers, blended with TextBlob. New anxious mood |
| Wake word was a substring ("remove", "memo", "emotion") | Whole-word regex; "Emo, add milk…" works in one breath |
| Reminders split on "at" (as in "eat", "that") and needed exact 24 h HH:MM | `timeparse.py`: am/pm, p.m., spoken numbers, relative times, noon, midnight, tonight, tomorrow, weekdays. Asks once if no time is given |
| "Open file" asked for the file name twice | Asks once, or uses the name you already gave |
| Duplicated, dead `fortune` and `fun mode` branches | Dispatch table of `cmd_*` methods |
| "Remind me to write…" became a note | Ordered, anchored intent rules; reminders take priority |
| "add" and "remove" matched "address" and "removed" | Whole-word patterns anchored to the start |
| Notes ran together (`+ ""`) | One timestamped line per note |
| Start could launch two assistant loops | Start is disabled while running; the session controller refuses a second start |
| Reminder popup created from a background thread | All UI updates go through a queue drained with `root.after()`; reminders show as non-modal toasts |
| `playsound` blocked the assistant | pygame, then a command-line player, then playsound in a daemon thread. "stop music" works |
| "Weekly" chart counted everything, and bars piled up | True last-7-days stacked chart, a new figure each time, `plt.close`, Agg backend |
| *Also found:* a bare "remove" deleted every to-do | Empty or unknown items remove nothing; ambiguous names ask for the number |
| *Also found:* the empty `data/mood_history.json` crashed `json.load` | Empty files read as empty; corrupt files are renamed `*.corrupt`; all writes are atomic |
| *Also found:* pyttsx3 used from several threads | One text-to-speech worker thread with a queue |
| *Also found:* mic re-calibrated on every command and listened forever with no timeout | Calibrates once; listens in short windows so typed input and Stop respond quickly |
| *Also found:* "stop" anywhere in a sentence quit, and that sentence still ran | "stop" must be the whole utterance |
| *Also found:* one failing command could kill the loop | Every command runs inside try/except |
| *Also found:* non-daemon threads kept the process alive after closing the window | All background threads are daemons and are stopped on exit |

On the first run, the old root files `mood_history.json` and `notes.txt` are merged into
`data/`. The originals are kept as `*.migrated`. Old mood formats (plain strings, `emotion`
keys, ISO timestamps) are read without errors.

## Settings

Copy `settings.example.json` to `settings.json` and edit it. Every key is optional, and invalid
values are ignored.

| Key | Default | Meaning |
|---|---|---|
| `user_name` | `""` | Used in greetings (`--name` overrides it) |
| `wake_words` | `["emo"]` | Add sound-alikes such as `"emma"` if recognition mishears you |
| `stop_words` | `["stop", "exit", "quit", "goodbye", "bye", "stop listening"]` | Whole-utterance quit words |
| `language` | `"en-US"` | Speech recognition language |
| `speech_rate` | `160` | Words per minute for speech output |
| `listen_timeout` / `phrase_time_limit` / `answer_timeout` | `1` / `10` / `10` s | Microphone timing |
| `music_file` | `"calm.mp3"` | Looked up in `assets/`, then the project root |
| `focus_minutes` | `25` | Default focus length |
| `history_limit` | `1000` | Mood entries kept |
| `open_images` | `true` | Terminal modes open charts and quotes in your image viewer |

## Project layout

```
emovoice.py              thin launcher: `python emovoice.py` still works
emovoice/
  __main__.py            argparse: GUI (default) / --text / --voice / --name
  config.py              absolute paths, Settings, find_asset(), legacy migration
  storage.py             thread-safe Store: moods, notes, to-dos, reminders, gratitude; atomic writes
  mood.py                detect_mood(text) -> MoodResult(mood, score, confidence)
  intents.py             parse_intent(text) -> Intent | None, wake/stop word helpers
  timeparse.py           parse_reminder(), parse_minutes(), say_time()
  reminders.py           ReminderScheduler and FocusTimer (check_now()/tick() for tests)
  speech.py              IO base class, TextIO, VoiceIO (mic + TTS worker + typed queue)
  system.py              cross-platform open_path/open_word/play_youtube/find_file, MusicPlayer
  visuals.py             mood chart, weekly summary, quote images
  content.py             jokes, fortunes, quotes, replies, help text
  assistant.py           Assistant: run(), handle(text), dispatch table of cmd_* methods
  gui.py                 Tkinter app; background threads talk to it only through an event queue
tests/test_core.py       pytest suite with fake IO, System and Player
assets/                  optional home for calm.mp3, motivational_bg.jpg
data/                    your data (git-ignored)
legacy/                  the original v1 script and README, for reference
```

The assistant only ever talks to an IO object (`say`, `listen`, `event`). Voice, terminal, GUI
and tests are just different IO implementations. The logic modules (intents, mood, time
parsing, storage) import without a microphone, Tkinter, pyttsx3 or internet.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

The suite has at least one test for every v1 bug and every new feature. It includes
parametrised tables for intents, mood detection and reminder parsing, all using a fixed clock.
It needs no microphone, speakers or internet. A GUI smoke test runs when a display is
available and is skipped otherwise.

## Privacy

Moods, notes, to-dos, reminders and gratitude entries stay on your computer in `data/`. Only
voice audio is sent, to Google's speech service, when you use the microphone.
