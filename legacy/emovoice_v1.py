import speech_recognition as sr
from textblob import TextBlob
import pyttsx3
import pyjokes
import os
import webbrowser
from playsound import playsound
import pywhatkit
import tkinter as tk
from tkinter import messagebox
import threading
import time
import datetime
import json
import random
from PIL import Image, ImageDraw, ImageFont
import matplotlib.pyplot as plt

engine = pyttsx3.init()
engine.setProperty('rate', 160)

wake_word = "emo"

mood_history_file = "mood_history.json"
todo_file = "data/todo.txt"

# Load mood history
if os.path.exists(mood_history_file):
    with open(mood_history_file, "r") as f:
        mood_history = json.load(f)
else:
    mood_history = []

def save_mood_history():
    with open(mood_history_file, "w") as f:
        json.dump(mood_history, f)

def speak(text):
    engine.say(text)
    engine.runAndWait()

def listen_command():
    r = sr.Recognizer()
    with sr.Microphone() as source:
        print("🎙️ Listening...")
        r.adjust_for_ambient_noise(source)
        audio = r.listen(source)
    try:
        text = r.recognize_google(audio)
        print(f"🗣️ You said: {text}")
        return text.lower()
    except sr.UnknownValueError:
        return ""
    except sr.RequestError:
        speak("Network error.")
        return ""

def wait_for_wake_word():
    r = sr.Recognizer()
    with sr.Microphone() as source:
        r.adjust_for_ambient_noise(source)
        while True:
            print("👂 Waiting for wake word 'Emo'...")
            audio = r.listen(source)
            try:
                text = r.recognize_google(audio).lower()
                print(f"🔍 Heard: {text}")
                if wake_word in text:
                    speak("I'm listening!")
                    return
            except sr.UnknownValueError:
                continue
            except sr.RequestError:
                speak("Wake word detection error.")
                break

def get_sentiment(text):
    blob = TextBlob(text)
    polarity = blob.sentiment.polarity
    if polarity < -0.3:
        return "sad"
    elif polarity > 0.3:
        return "happy"
    else:
        return "angry" if "!" in text else "neutral"

def generate_motivation_image():
    quotes = ["Believe in yourself!", "You are doing great!", "Keep pushing forward!", "Stay strong."]
    quote = random.choice(quotes)
    img = Image.new('RGB', (500, 200), color=(73, 109, 137))
    fnt = ImageFont.load_default()
    d = ImageDraw.Draw(img)
    d.text((10, 80), quote, font=fnt, fill=(255, 255, 0))
    img_path = "motivational_quote.png"
    img.save(img_path)
    os.startfile(img_path)

def tell_daily_fortune():
    fortunes = [
        "Today is a great day to start something new.",
        "Be kind to yourself and good things will happen.",
        "You might receive unexpected news today.",
        "Focus on your goals and stay consistent.",
        "Something joyful is coming your way."
    ]
    fortune = random.choice(fortunes)
    speak(f"Here's your fortune: {fortune}")

def fun_mode():
    responses = [
        "Well well well, look who's back!", 
        "I'm your funky assistant now, ready to groove!", 
        "Did someone order sass with a side of code?"
    ]
    speak(random.choice(responses))

def update_todo(command):
    if "add" in command:
        item = command.replace("add", "").replace("to my to-do list", "").strip()
        with open(todo_file, "a") as f:
            f.write(item + "\n")
        speak("Added to your to-do list.")
    elif "remove" in command:
        item = command.replace("remove", "").strip()
        if os.path.exists(todo_file):
            with open(todo_file, "r") as f:
                tasks = f.readlines()
            with open(todo_file, "w") as f:
                for task in tasks:
                    if item not in task:
                        f.write(task)
            speak("Item removed.")
    elif "what's on my list" in command or "show list" in command:
        if os.path.exists(todo_file):
            with open(todo_file, "r") as f:
                tasks = f.readlines()
            if tasks:
                speak("Here is your to-do list.")
                for task in tasks:
                    speak(task.strip())
            else:
                speak("Your list is empty.")
        else:
            speak("Your list is empty.")

def plot_mood_chart():
    if not mood_history:
        speak("No mood data yet to show.")
        return
    moods = {}
    for entry in mood_history:
        mood = entry["mood"]
        moods[mood] = moods.get(mood, 0) + 1
    plt.bar(moods.keys(), moods.values(), color='purple')
    plt.title("Weekly Mood Tracker")
    plt.xlabel("Mood")
    plt.ylabel("Frequency")
    plt.tight_layout()
    chart_file = "mood_chart.png"
    plt.savefig(chart_file)
    os.startfile(chart_file)

def execute_command(text):
    print(f"[DEBUG] Executing command: {text}")
    if "open youtube" in text:
        title = text.replace("open youtube", "").strip()
        if title:
            speak(f"Playing {title} on YouTube")
            pywhatkit.playonyt(title)
        else:
            speak("Opening YouTube")
            webbrowser.open("https://youtube.com")

    elif "open word" in text:
        speak("Opening Microsoft Word")
        os.system("start winword")

    elif "write" in text:
        content = text.replace("write", "").strip()
        with open("notes.txt", "a") as f:
            f.write(content + "")
        speak("Written to file.")

    elif "play music" in text:
        speak("Playing calming music.")
        playsound("calm.mp3")

    elif "fortune" in text:
        speak("Here's your daily fortune.")
        tell_daily_fortune()

    elif "fun mode" in text:
        speak("Fun mode activated!")
        fun_mode()

    elif "open" in text and "file" in text:
        speak("What file should I open?")
        file_name = listen_command()
        if file_name:
            try:
                os.startfile(file_name)
                speak(f"Opening {file_name}")
            except Exception as e:
                speak(f"Could not open {file_name}. Please check the file name.")
        else:
            speak("I didn't catch the file name.")
        speak("What file should I open?")
        file_name = listen_command()
        if file_name:
            try:
                os.startfile(file_name)
                speak(f"Opening {file_name}")
            except Exception as e:
                speak(f"Could not open {file_name}. Please check the file name.")
        else:
            speak("I didn't catch the file name.")

    elif "remind me" in text and "at" in text:
        try:
            reminder_text = text.split("remind me")[1].split("at")[0].strip()
            reminder_time = text.split("at")[1].strip()
            schedule_reminder(reminder_time, reminder_text)
            speak(f"Reminder set for {reminder_time} to {reminder_text}")
        except:
            speak("I couldn't set the reminder. Please try again with a valid format.")

    elif "add" in text or "remove" in text or "to-do list" in text or "show list" in text:
        speak("Managing your to-do list.")
        update_todo(text)

    elif "show my mood chart" in text:
        speak("Here is your mood chart.")
        plot_mood_chart()

    elif "motivate" in text:
        speak("Generating your motivational quote.")
        generate_motivation_image()

    elif "fortune" in text:
        speak("Here's your daily fortune.")
        tell_daily_fortune()

    elif "fun mode" in text:
        speak("Fun mode activated!")
        fun_mode()

    else:
        speak("I didn't understand the command.")

def react_to_sentiment(mood):
    mood_history.append({"timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "mood": mood})
    if len(mood_history) > 100:
        mood_history.pop(0)
    save_mood_history()

    if mood == "sad":
        speak("You sound sad. Here's a joke to cheer you up!")
        speak(pyjokes.get_joke())
    elif mood == "angry":
        speak("You sound angry. Let's take a few deep breaths together.")
        speak("Inhale... Exhale... Inhale... Exhale...")
        playsound("calm.mp3")
    elif mood == "happy":
        speak("It's good to see you happy! I'm always here for you.")
    else:
        speak("How can I help you today?")

def schedule_reminder(reminder_time, message):
    def reminder():
        while True:
            now = datetime.datetime.now().strftime("%H:%M")
            if now == reminder_time:
                speak(f"Reminder: {message}")
                messagebox.showinfo("Reminder", f"{message}")
                break
            time.sleep(30)
    threading.Thread(target=reminder).start()

def main():
    speak("Hello, I'm your EmoVoice Assistant. Say 'stop' to end.")
    wait_for_wake_word()
    keywords = [
        "open youtube", "open word", "write", "play music", "open file", "remind me",
        "add", "remove", "to-do list", "show list", "motivate", "fortune", "fun mode", "show my mood chart"
    ]
    text = ""
    while "stop" not in text:
        text = listen_command()
        if text:
            if any(k in text for k in keywords) or text.strip() in ["i am sad", "i am angry", "i'm sad", "i'm angry", "i'm happy"]:
                speak("Got it! Let me handle that.")
                execute_command(text)
            else:
                mood = get_sentiment(text)
                react_to_sentiment(mood)
                execute_command(text)
        else:
            speak("I didn't catch that. Try again.")
    speak("Goodbye! Take care.")

# GUI
class AssistantGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("EmoVoice Assistant")
        self.label = tk.Label(root, text="🎤 Say 'Emo' to activate", font=("Arial", 14))
        self.label.pack(pady=10)
        self.button = tk.Button(root, text="Start Assistant", command=self.run_assistant, font=("Arial", 12))
        self.button.pack(pady=10)

    def run_assistant(self):
        threading.Thread(target=main).start()

if __name__ == "__main__":
    root = tk.Tk()
    gui = AssistantGUI(root)
    root.mainloop()
