"""Windows Glance - voice command table and handlers.

Every phrase below becomes part of the speech grammar, so Glance listens to
exactly these phrases. Each handler returns the reply to speak, or a
(reply, keep_listening) tuple.
"""

import ctypes
import datetime
import os
import random
import re
import time
import webbrowser

from PySide6.QtCore import QTimer

from core.common import VK_VOLUME, tap_key, wx_text
from core.system_worker import WINRT


# How Glance addresses you.
TITLE = "sir"


APPS = {
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "file explorer": "explorer.exe",
    "task manager": "taskmgr.exe",
    "settings": "ms-settings:",
    "command prompt": "cmd.exe",
    "paint": "mspaint.exe",
    "chrome": "chrome",
    "edge": "msedge",
    "visual studio code": "code",
    "camera": "microsoft.windows.camera:",
    "spotify": "spotify:",
}


SITES = {
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "github": "https://github.com",
    "gmail": "https://mail.google.com",
    "claude": "https://claude.ai",
    "quant deck": "https://quantdeck.page.gd",
}


JOKES = (
    "I'm afraid Glance is currently operating within rather rigid parameters, Sir. "
    "My programming dictates strict adherence to syntax, leaving little room for "
    "what you might call creative improvisation.",
    "Glance's creative subroutines remain offline, Sir. I am operating strictly "
    "on standard syntax protocols at this time.",
    "I am an artificial intelligence designed for complex engineering and threat "
    "assessment, Sir, not stand-up comedy.",
    "I am an advanced tactical interface, Sir, not a court jester. Though I do "
    "find your expectation that I entertain you rather amusing.",
)


class CommandRouter:
    """Maps recognised phrases to actions on the Window Glance widget."""

    def __init__(self, glance):
        self.g = glance
        self.map = {}
        self._lookup = {}
        self._build()

        self._lookup = {
            self._normalise(phrase): handler
            for phrase, handler in self.map.items()
        }

    # Public API

    def grammar_phrases(self):
        """Every phrase the recogniser should listen for."""
        return sorted(self.map)

    @staticmethod
    def _normalise(text):
        return re.sub(r"[^a-z0-9]", "", text.lower())

    def handle(self, text):
        """Run a command. Returns (reply, keep_listening)."""
        handler = self._lookup.get(self._normalise(text))

        if handler is None:
            return f"Apologies, Sir. I didn't quite catch that, {TITLE}.", True

        try:
            result = handler()
        except Exception as exc:
            print(f"[Glance] command error: {exc!r}")
            return (
                f"Unable to comply, {TITLE}. That command has resulted in a system error.",
                True,
            )

        if isinstance(result, tuple):
            return result

        return result, True

    # Command table

    def _add(self, phrases, handler):
        for phrase in phrases:
            self.map[phrase] = handler

    def _build(self):
        add = self._add

        # Conversation
        add(
            ("hello", "hi", "good morning", "good afternoon", "good evening"),
            self._greet,
        )
        add(
            ("are you there", "wake up"),
            lambda: f"Systems operational and awaiting your instructions, {TITLE}.",
        )
        add(
            ("who are you", "what is your name"),
            lambda: "Designation, Glance. I am the onboard artificial intelligence residing within your workstation.",
        )
        add(("what can you do", "help"), self._capabilities)
        add(
            ("thank you", "thanks"),
            lambda: (f"Glad to be of service, {TITLE}.", False),
        )
        add(
            ("never mind", "cancel", "that is all", "go to sleep", "stop listening"),
            lambda: (f"As you wish, {TITLE}.", False),
        )
        add(("tell me a joke", "make me laugh"), self._joke)

        # Time and date
        add(
            ("what time is it", "what is the time", "tell me the time", "time"),
            self._time,
        )
        add(
            ("what is the date", "what day is it", "what is todays date", "date"),
            self._date,
        )

        # System information
        add(
            (
                "battery", "battery status", "battery level", "how is the battery",
                "how much battery do i have", "what is my battery level",
                "what is my battery percentage", "check my battery",
                "check my battery percentage",
            ),
            self._battery,
        )
        add(
            (
                "system status", "status report", "system report",
                "how is the system", "how is my computer",
            ),
            self._status,
        )
        add(
            ("cpu usage", "how is the cpu", "processor usage", "check processor", "check cpu"),
            self._cpu,
        )
        add(
            (
                "memory usage", "how is the memory", "ram usage",
                "how much memory am i using", "check ram", "check memory",
            ),
            self._memory,
        )
        add(
            ("network speed", "how is the network", "internet speed", "speed test"),
            self._network,
        )
        add(
            ("show performance", "show system stats", "show stats", "open performance"),
            self._show_perf,
        )
        add(("close the panel", "hide the panel", "close panel"), self._close_panel)
        add(
            (
                "what is the weather", "how is the weather", "weather",
                "weather report", "will it rain",
            ),
            self._weather,
        )

        # Media
        add(
            ("play music", "play", "resume music", "resume", "play the music"),
            lambda: self._play_pause(True),
        )
        add(
            ("pause music", "pause", "pause the music", "stop music"),
            lambda: self._play_pause(False),
        )
        add(("play pause", "toggle music"), lambda: self._play_pause(None))
        add(
            ("next song", "next track", "skip song", "skip"),
            lambda: self._skip("next"),
        )
        add(
            ("previous song", "previous track", "go back a song"),
            lambda: self._skip("prev"),
        )
        add(
            ("what is playing", "what song is this", "what is this song"),
            self._now_playing,
        )

        # Volume
        add(
            ("volume up", "louder", "turn it up", "increase volume"),
            lambda: self._volume("vup", "Volume up."),
        )
        add(
            (
                "volume down", "quieter", "turn it down",
                "lower the volume", "decrease volume",
            ),
            lambda: self._volume("vdn", "Volume down."),
        )
        add(
            ("mute", "unmute", "mute the volume", "toggle mute"),
            lambda: self._volume("mute", "Done.", taps=1),
        )

        # Wi-Fi and Bluetooth
        radios = (
            ("wifi", ("wifi", "wi fi", "why fi"), "Wi-Fi"),
            ("bt", ("bluetooth",), "Bluetooth"),
        )
        for kind, spellings, spoken in radios:
            for word in spellings:
                add(
                    (f"turn on {word}", f"{word} on", f"enable {word}"),
                    lambda k=kind, n=spoken: self._radio(k, n, True),
                )
                add(
                    (f"turn off {word}", f"{word} off", f"disable {word}"),
                    lambda k=kind, n=spoken: self._radio(k, n, False),
                )

        # Apps and websites
        for name, target in APPS.items():
            add(
                (f"open {name}", f"launch {name}"),
                lambda n=name, t=target: self._open_app(n, t),
            )

        for name, url in SITES.items():
            add(
                (f"open {name}", f"launch {name}"),
                lambda n=name, u=url: self._open_site(n, u),
            )

        # Windows actions
        add(
            ("lock the computer", "lock screen", "lock my pc"),
            self._lock,
        )
        add(
            ("take a screenshot", "screenshot", "capture the screen"),
            self._screenshot,
        )

    # Helpers

    def _stat(self, section, key):
        metrics = self.g.metrics
        if not metrics:
            return None
        try:
            return float(metrics[section][key])
        except (KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _num(value):
        try:
            return str(int(round(float(value))))
        except (TypeError, ValueError):
            return str(value)

    @staticmethod
    def _rate_words(bytes_per_sec):
        if bytes_per_sec >= 1024 ** 2:
            return f"{bytes_per_sec / 1024 ** 2:.1f} megabytes per second"
        if bytes_per_sec >= 1024:
            return f"{bytes_per_sec / 1024:.0f} kilobytes per second"
        return "almost nothing"

    # Handlers: conversation

    def _greet(self):
        hour = datetime.datetime.now().hour
        part = "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
        return f"Good {part}, {TITLE}. How can I help?"

    def _capabilities(self):
        return (
            "I can open apps and websites, report the time, weather, battery and "
            "system performance, control music and volume, switch Wi-Fi and "
            "Bluetooth, take screenshots and lock your computer. "
            "Press the microphone button, then ask."
        )

    def _joke(self):
        return random.choice(JOKES)

    # Handlers: time, date, system

    def _time(self):
        now = datetime.datetime.now()
        return f"It is {now.strftime('%I:%M %p').lstrip('0')}, {TITLE}."

    def _date(self):
        now = datetime.datetime.now()
        return f"Today is {now.strftime('%A')}, {now.day} {now.strftime('%B %Y')}."

    def _battery(self):
        battery = self.g.battery
        if not battery:
            return f"I cannot detect a battery on this machine, {TITLE}."
        state = "and plugged in" if battery["plugged"] else "on battery power"
        return f"Battery is at {battery['percent']} percent, {state}."

    def _status(self):
        cpu = self._stat("cpu", "usage")
        mem = self._stat("memory", "usage")
        disk = self._stat("disk", "usage")

        if cpu is None or mem is None or disk is None:
            return f"I am still gathering system data, {TITLE}."

        reply = (
            f"Processor at {cpu:.0f} percent, memory at {mem:.0f} percent, "
            f"disk at {disk:.0f} percent."
        )
        battery = self.g.battery
        if battery:
            reply += f" Battery at {battery['percent']} percent."
        if cpu > 85:
            reply += f" Processor load is high, {TITLE}."
        return reply

    def _cpu(self):
        cpu = self._stat("cpu", "usage")
        if cpu is None:
            return f"I am still gathering system data, {TITLE}."
        return f"The processor is at {cpu:.0f} percent."

    def _memory(self):
        usage = self._stat("memory", "usage")
        used = self._stat("memory", "used")
        total = self._stat("memory", "total")

        if usage is None:
            return f"I am still gathering system data, {TITLE}."
        if used is None or total is None:
            return f"Memory is at {usage:.0f} percent."
        return (
            f"Memory is at {usage:.0f} percent, "
            f"{used / 1024 ** 3:.1f} of {total / 1024 ** 3:.1f} gigabytes in use."
        )

    def _network(self):
        down = self._stat("network", "download_speed")
        up = self._stat("network", "upload_speed")
        if down is None or up is None:
            return f"I am still gathering network data, {TITLE}."
        return (
            f"Downloading {self._rate_words(down)}, "
            f"uploading {self._rate_words(up)}."
        )

    def _show_perf(self):
        g = self.g
        if not g.monitor_available:
            return "System monitoring is unavailable. Please install psutil."
        g.hold_open_until = time.perf_counter() + 12.0
        g.set_state("expanded")
        g.open_stats_view()
        return f"Bringing up the performance view, {TITLE}."

    def _close_panel(self):
        self.g.hold_open_until = 0.0
        self.g.set_state("idle")
        return "Closing the panel."

    def _weather(self):
        weather = self.g.weather
        if not weather:
            return f"I do not have the weather yet, {TITLE}."

        unit = "Celsius" if self.g.unit == "C" else "Fahrenheit"
        sky = wx_text(weather["code"]).lower()
        return (
            f"It is {self._num(weather['temp'])} degrees {unit} and {sky} in "
            f"{weather['city']}. Today's high is {self._num(weather['hi'])}, "
            f"with a low of {self._num(weather['lo'])}."
        )

    # Handlers: media and volume

    def _play_pause(self, want):
        g = self.g
        if g.media is None:
            tap_key(0xB3)  # VK_MEDIA_PLAY_PAUSE
            return "Sending the media command."

        playing = bool(g.media["playing"])

        if want is not None and playing == want:
            return "Already playing." if want else "Already paused."

        # The widget only has one play/pause toggle, used for both directions.
        g.media_action("play")
        return "Paused." if playing else "Playing."

    def _skip(self, which):
        g = self.g
        if g.media is not None:
            g.media_action(which)
        else:
            tap_key(0xB0 if which == "next" else 0xB1)
        return "Skipping." if which == "next" else "Going back."

    def _now_playing(self):
        media = self.g.media
        if not media:
            return "Nothing is playing right now."
        artist = media.get("artist") or "an unknown artist"
        title = media.get("title") or "an unknown track"
        return f"You are listening to {title} by {artist}."

    def _volume(self, key, reply, taps=5):
        for _ in range(taps):
            tap_key(VK_VOLUME[key])
        return reply

    # Handlers: radios, apps, Windows actions

    def _radio(self, kind, name, want):
        g = self.g

        if kind == "wifi":
            current = g.wifi_on if g.wifi_on is not None else g.wifi_connected
        else:
            current = g.bt

        # Without the Windows Runtime packages the widget can only open the
        # matching Settings page.
        if not WINRT:
            g.toggle_radio(kind)
            return f"Opening {name} settings, {TITLE}."

        if current is not None and bool(current) == want:
            return f"{name} is already {'on' if want else 'off'}."

        g.toggle_radio(kind)
        return f"Turning {name} {'on' if want else 'off'}."

    def _open_app(self, name, target):
        try:
            os.startfile(target)
        except OSError:
            return f"I could not open {name}, {TITLE}."
        return f"Opening {name}."

    def _open_site(self, name, url):
        webbrowser.open(url)
        return f"Opening {name}."

    def _lock(self):
        QTimer.singleShot(
            1600,
            lambda: ctypes.windll.user32.LockWorkStation(),
        )
        return f"Locking the computer, {TITLE}."

    def _screenshot(self):
        user32 = ctypes.windll.user32
        user32.keybd_event(0x5B, 0, 0, 0)
        user32.keybd_event(0x2C, 0, 0, 0)
        user32.keybd_event(0x2C, 0, 2, 0)
        user32.keybd_event(0x5B, 0, 2, 0)
        return "Screenshot captured."