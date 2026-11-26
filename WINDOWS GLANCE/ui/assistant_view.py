"""
Windows Glance - the "Glance" voice assistant (Jarvis-style).

Say "Glance" to wake it, then a command ("what is the battery", "open
notepad", ...). While it listens, thinks or speaks, the pill pops out and shows
an animated arc-reactor orb with a live caption.

Hooked into engine.py through AssistantMixin. Needs no extra packages: it uses
the speech engine built into Windows.

Test without a microphone (PowerShell):
    $env:GLANCE_SAY = "what is the battery"; python main.py
"""

import datetime
import math
import os
import threading
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QPen,
    QRadialGradient,
)

from core.commands import TITLE, CommandRouter
from core.common import clamp, draw_text, ui_font, with_alpha
from core.voice import VoiceIO


WAKE_WORD = "glance"

WAKE_CONFIDENCE = 0.55       # lower = easier to wake, more false wakes
COMMAND_CONFIDENCE = 0.65    # lower = accepts shakier commands
AWAKE_SECONDS = 7.0          # time to give a command after the wake word
FOLLOWUP_SECONDS = 5.0       # follow-up window after an answer

CHIME = True                 # short beep when Glance wakes
STARTUP_GREETING = True      # spoken greeting when the app starts

ASSIST_W = 320
ASSIST_H = 52

LABELS = {
    "listening": "LISTENING",
    "thinking": "PROCESSING",
    "speaking": "SPEAKING",
    "notice": "NOTICE",
}


def _beep():
    try:
        import winsound

        winsound.Beep(880, 55)
        winsound.Beep(1320, 70)
    except Exception:
        pass


class AssistantMixin:
    """Voice assistant state machine and HUD painting."""

    # Class-level defaults so attributes exist before init_assistant().
    assistant_active = False     # pill is showing the assistant
    _assist_paint = False        # keep painting while the pill shrinks
    assist_state = "off"         # off, listening, thinking, speaking, notice
    _assist_draw_state = "listening"
    assist_caption = ""
    _assist_cap_t = 0.0
    _assist_shown_at = 0.0
    _awake_until = 0.0
    _mic_ignore_until = 0.0
    _follow_up = True
    _error_shown = False
    hold_open_until = 0.0

    # ------------------------------------------------------------------ #
    # Setup
    # ------------------------------------------------------------------ #

    def init_assistant(self):
        self.assist_router = CommandRouter(self)

        self.voice = VoiceIO(self)
        self.voice.heard.connect(self._on_heard)
        self.voice.speech_done.connect(self._on_speech_done)
        self.voice.status.connect(self._on_voice_status)

        self._assist_timer = QTimer(self)
        self._assist_timer.setInterval(33)
        self._assist_timer.timeout.connect(self._assist_tick)

        self.voice.start(self.assist_router.grammar_phrases(WAKE_WORD))

        if STARTUP_GREETING:
            QTimer.singleShot(3000, self._startup_greeting)

        test = os.environ.get("GLANCE_SAY")

        if test:
            QTimer.singleShot(7000, lambda: self._run_command(test.lower()))

    def shutdown_assistant(self):
        self._assist_timer.stop()
        self.voice.shutdown()

    def assistant_size(self):
        return float(ASSIST_W), float(ASSIST_H)

    # ------------------------------------------------------------------ #
    # Hearing
    # ------------------------------------------------------------------ #

    def _on_heard(self, text, conf):
        now = time.perf_counter()
        t = " ".join(text.lower().split())

        print(f"[Glance] heard '{t}' ({conf:.2f})")

        # Ignore the microphone while Glance is busy (or hearing itself).
        if self.voice.speaking or now < self._mic_ignore_until:
            return

        if self.assist_state in ("thinking", "speaking"):
            return

        if t == WAKE_WORD:
            if conf >= WAKE_CONFIDENCE:
                self._wake()

            return

        prefix = WAKE_WORD + " "

        if t.startswith(prefix):
            if conf >= COMMAND_CONFIDENCE:
                self._run_command(t[len(prefix):])

            return

        if now < self._awake_until and conf >= COMMAND_CONFIDENCE:
            self._run_command(t)

    def _on_voice_status(self, message, is_error):
        print(f"[Glance voice] {message}")

        if is_error and not self._error_shown:
            self._error_shown = True
            self._awake_until = time.perf_counter() + 4.0
            self._set_assist("notice", message[:60])
            self._show_assist()

    # ------------------------------------------------------------------ #
    # Conversation flow
    # ------------------------------------------------------------------ #

    def _chime(self):
        if CHIME:
            threading.Thread(target=_beep, daemon=True).start()

    def _wake(self):
        self._chime()
        self._awake_until = time.perf_counter() + AWAKE_SECONDS
        self._set_assist("listening", "Listening\u2026")
        self._show_assist()

    def _run_command(self, cmd):
        cmd = cmd.strip()

        if not cmd:
            return

        self._awake_until = 0.0
        self._set_assist("thinking", cmd.capitalize())
        self._show_assist()

        reply, follow = self.assist_router.handle(cmd)
        self._follow_up = follow

        QTimer.singleShot(280, lambda r=reply: self._deliver(r))

    def _deliver(self, reply):
        self._set_assist("speaking", reply)
        self.voice.say(reply)

    def _announce(self, text):
        self._follow_up = False
        self._set_assist("speaking", text)
        self._show_assist()
        self.voice.say(text)

    def _startup_greeting(self):
        hour = datetime.datetime.now().hour
        part = "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"

        self._announce(
            f"Good {part}, {TITLE}. Glance is online and all systems are operational."
        )

    def _on_speech_done(self):
        now = time.perf_counter()
        self._mic_ignore_until = now + 0.7

        if self.assist_state != "speaking":
            return

        if self._follow_up:
            self._awake_until = now + FOLLOWUP_SECONDS
            self._set_assist("listening", "Listening\u2026")
        else:
            self._end_assist()

    def _set_assist(self, state, caption):
        self.assist_state = state
        self._assist_draw_state = state
        self.assist_caption = caption
        self._assist_cap_t = time.perf_counter()

        if not self._assist_timer.isActive():
            self._assist_timer.start()

        self.update()

    def _end_assist(self):
        self.assist_state = "off"
        self._assist_timer.stop()
        self.hide_assistant()

    def _assist_tick(self):
        now = time.perf_counter()

        if self.assist_state == "off":
            self._assist_timer.stop()
            return

        if self.assist_state in ("listening", "notice") and now >= self._awake_until:
            self._end_assist()
            return

        if self.assistant_active and self.isVisible():
            self.update()

    # ------------------------------------------------------------------ #
    # Pill visibility
    # ------------------------------------------------------------------ #

    def _show_assist(self):
        if self.state == "expanded" or self.assistant_active:
            return

        self.end_charge_activity(immediate=True)

        self.assistant_active = True
        self._assist_paint = True
        self._assist_shown_at = time.perf_counter()

        self.retarget(480)
        self.update()

    def hide_assistant(self, immediate=False):
        if not self.assistant_active and not self._assist_paint:
            return

        self.assistant_active = False

        if immediate:
            self._assist_paint = False
            return

        self.retarget(320)
        self.update()

        # Keep painting the content while the pill shrinks.
        QTimer.singleShot(450, self._assist_clear_paint)

    def _assist_clear_paint(self):
        if not self.assistant_active:
            self._assist_paint = False
            self.update()

    def start_charge_activity(self, kind):
        # Do not interrupt Glance with the charging animation.
        if self.assistant_active or self.assist_state != "off":
            return

        super().start_charge_activity(kind)

    # ------------------------------------------------------------------ #
    # Painting
    # ------------------------------------------------------------------ #

    def _paint_compact(self, p, card):
        if self._assist_paint and self.state != "expanded":
            self._paint_assist(p, card)
            return

        super()._paint_compact(p, card)

    def _paint_assist(self, p, card):
        th = self.th
        now = time.perf_counter()
        state = self._assist_draw_state

        hud = QColor("#4FD8FF") if self.dark else QColor("#0A8FBF")

        if state == "notice":
            hud = QColor(th.bad)

        p.save()
        p.setOpacity(
            p.opacity() * clamp((now - self._assist_shown_at) / 0.25)
        )

        cy = card.top() + min(card.height(), ASSIST_H) / 2
        x0 = card.left() + 16
        centre = QPointF(x0 + 19, cy)
        radius = 17.0

        # Orb behaviour per state.
        if state == "speaking":
            amp = 0.2 + 0.8 * abs(
                math.sin(now * 11.0) * math.sin(now * 4.3 + 1.0)
            )
            spin = 110.0

        elif state == "thinking":
            amp = 0.5 + 0.5 * math.sin(now * 8.0)
            spin = 260.0

        else:
            amp = 0.5 + 0.5 * math.sin(now * 2.4)
            spin = 45.0

        # Soft glow.
        glow = QRadialGradient(centre, radius * 1.9)
        glow.setColorAt(0.0, with_alpha(hud, int(60 + 70 * amp)))
        glow.setColorAt(1.0, with_alpha(hud, 0))

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(glow))
        p.drawEllipse(centre, radius * 1.9, radius * 1.9)

        # Outer ring and slowly counter-rotating tick marks.
        p.setPen(QPen(with_alpha(hud, 140), 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(centre, radius, radius)

        p.save()
        p.translate(centre)
        p.rotate(-now * spin * 0.35)
        p.setPen(QPen(with_alpha(hud, 120), 1.0))

        for _ in range(24):
            p.drawLine(QPointF(radius + 1.5, 0), QPointF(radius + 3.5, 0))
            p.rotate(15)

        p.restore()

        # Three spinning arc segments.
        arc_rect = QRectF(centre.x() - 13, centre.y() - 13, 26, 26)

        pen = QPen(hud, 2.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)

        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)

        base = (now * spin) % 360

        for k in range(3):
            p.drawArc(arc_rect, int((base + k * 120) * 16), int(64 * 16))

        # Inner ring and pulsing core.
        p.setPen(QPen(with_alpha(hud, 160), 1.0))
        p.drawEllipse(centre, 8.5, 8.5)

        core_r = 3.5 + 3.0 * amp
        core = QRadialGradient(centre, core_r * 1.8)

        core.setColorAt(
            0.0,
            QColor(255, 255, 255) if self.dark else hud.lighter(140),
        )
        core.setColorAt(0.55, hud)
        core.setColorAt(1.0, with_alpha(hud, 0))

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(core))
        p.drawEllipse(centre, core_r * 1.8, core_r * 1.8)

        # Text: small caps label and caption.
        tx = x0 + 46
        avail = ASSIST_W - 62 - 16

        label_font = QFont(ui_font(9, QFont.Weight.DemiBold))
        label_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.4)

        draw_text(
            p,
            f"GLANCE  \u00b7  {LABELS.get(state, 'STANDBY')}",
            tx,
            cy - 6,
            label_font,
            with_alpha(hud, 230),
        )

        self._draw_caption(
            p,
            self.assist_caption,
            tx,
            cy + 13,
            avail,
            ui_font(12),
            th.text,
            now - self._assist_cap_t,
        )

        p.restore()

    def _draw_caption(self, p, text, x, base, width, font, colour, elapsed):
        """Single-line caption that scrolls when it is too long to fit."""

        fm = QFontMetricsF(font)
        text_w = fm.horizontalAdvance(text)

        p.save()

        p.setClipRect(
            QRectF(x, base - fm.ascent(), width, fm.height()),
            Qt.ClipOperation.IntersectClip,
        )

        if text_w <= width:
            draw_text(p, text, x, base, font, colour)

        else:
            gap = 40.0
            shift = (max(0.0, elapsed - 0.6) * 38.0) % (text_w + gap)

            draw_text(p, text, x - shift, base, font, colour)
            draw_text(p, text, x - shift + text_w + gap, base, font, colour)

        p.restore()