"""
Windows Glance - the "Glance" voice assistant.

Say "Glance" (or press the microphone button), then give a command. While it
listens, thinks or speaks, the pill pops out and shows a fluid, Siri-style
orb that reacts to your real microphone level, a live caption of what Windows
hears, and a stop/mic button.

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
    QImage,
    QPainter,
    QPainterPath,
    QRadialGradient,
)

from core.commands import TITLE, CommandRouter
from core.common import (
    PILL_H,
    clamp,
    draw_glyph,
    draw_text,
    set_click_through,
    ui_font,
    with_alpha,
)
from core.voice import VoiceIO


WAKE_WORD = "glance"

WAKE_CONFIDENCE = 0.55       # lower = easier to wake, more false wakes
COMMAND_CONFIDENCE = 0.65    # lower = accepts shakier commands
AWAKE_SECONDS = 7.0          # time to give a command after the wake word
MANUAL_SECONDS = 10.0        # time to speak after pressing the mic button
FOLLOWUP_SECONDS = 5.0       # follow-up window after an answer

CHIME = True                 # short beep when Glance starts listening
STARTUP_GREETING = True      # spoken greeting when the app starts

ASSIST_W = 320
ASSIST_H = 52
MIC_SLOT = 52                # extra width of the hover pill for the mic button

ORB_SIZE = 60                # orb canvas, logical pixels
ORB_RADIUS = 15.0

# (colour, phase, speed) for the blended light blobs inside the orb.
ORB_COLORS = (
    (QColor(56, 214, 255), 0.00, 0.80),    # cyan
    (QColor(112, 92, 255), 2.10, 1.15),    # violet
    (QColor(255, 80, 214), 4.20, 0.95),    # magenta
    (QColor(255, 163, 82), 1.00, 0.65),    # amber
)

ORB_ALERT = (
    (QColor(255, 96, 96), 0.00, 0.80),
    (QColor(255, 150, 80), 2.10, 1.15),
    (QColor(210, 60, 120), 4.20, 0.95),
    (QColor(255, 200, 120), 1.00, 0.65),
)

LABELS = {
    "listening": "LISTENING",
    "thinking": "PROCESSING",
    "speaking": "SPEAKING",
    "notice": "ERROR",
}


def _beep():
    try:
        import winsound

        winsound.Beep(880, 55)
        winsound.Beep(1320, 70)
    except Exception:
        pass


class AssistantMixin:
    """Voice assistant state machine, mic button and orb painting."""

    # Class-level defaults so attributes exist before init_assistant().
    assistant_active = False     # pill is showing the assistant
    _assist_paint = False        # keep painting while the pill shrinks
    assist_state = "off"         # off, listening, thinking, speaking, notice
    _assist_draw_state = "listening"
    assist_caption = ""
    assist_hyp = ""
    mic_level = 0.0
    _hyp_t = 0.0
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
        self.voice.hypothesis.connect(self._on_hypothesis)
        self.voice.level.connect(self._on_level)
        self.voice.rejected.connect(self._on_rejected)
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
    # Mic button geometry (card coordinates)
    # ------------------------------------------------------------------ #

    def mic_rect(self):
        card = self._card

        if self.state == "expanded":
            if self.stats_view:
                return None

            mute = self.vol_rects()["mute"]

            return QRectF(mute.left() - 36, mute.top(), 32, 32)

        if self.assistant_active:
            return QRectF(card.width() - 44, ASSIST_H / 2 - 14, 28, 28)

        if self.state == "hover":
            return QRectF(card.width() - 38, PILL_H / 2 - 13, 26, 26)

        return None

    def toggle_listening(self):
        """Mic button: start a listening session, or stop whatever is running."""

        state = self.assist_state

        if state == "off":
            if self.state == "expanded":
                self.set_state("hover")

            self._chime()
            self._awake_until = time.perf_counter() + MANUAL_SECONDS
            self.assist_hyp = ""
            self._set_assist("listening", "Listening\u2026")
            self._show_assist()
            return

        self._awake_until = 0.0
        self.mic_level = 0.0
        self.assist_hyp = ""

        if state == "speaking" or self.voice.speaking:
            self.voice.stop_speaking()

        self._end_assist()

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

    def _on_hypothesis(self, text):
        if self.assist_state == "listening" and not self.voice.speaking:
            self.assist_hyp = text.capitalize()
            self._hyp_t = time.perf_counter()

    def _on_rejected(self):
        if self.assist_state == "listening" and not self.voice.speaking:
            self.assist_hyp = "Not recognised"
            self._hyp_t = time.perf_counter()

    def _on_level(self, value):
        if self.assist_state == "listening":
            self.mic_level = max(self.mic_level, clamp(value / 55.0))

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
        self.assist_hyp = ""
        self._set_assist("listening", "Listening\u2026")
        self._show_assist()

    def _run_command(self, cmd):
        cmd = cmd.strip()

        if not cmd:
            return

        self._awake_until = 0.0
        self.assist_hyp = ""
        self._set_assist("thinking", cmd.capitalize())
        self._show_assist()

        reply, follow = self.assist_router.handle(cmd)
        self._follow_up = follow

        QTimer.singleShot(280, lambda r=reply: self._deliver(r))

    def _deliver(self, reply):
        if self.assist_state != "thinking":
            return  # cancelled with the mic button

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
        self._mic_ignore_until = now + 0.9

        if self.assist_state != "speaking":
            return

        if self._follow_up:
            self._awake_until = now + FOLLOWUP_SECONDS
            self.assist_hyp = ""
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
        self.mic_level = 0.0
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

        self.mic_level *= 0.86

        if self.mic_level < 0.01:
            self.mic_level = 0.0

        if self.isVisible():
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

        # The pill must accept clicks so the mic / stop button works.
        set_click_through(self, False)

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
            set_click_through(self, self.state == "idle")
            self.update()

    def start_charge_activity(self, kind):
        # Do not interrupt Glance with the charging animation.
        if self.assistant_active or self.assist_state != "off":
            return

        super().start_charge_activity(kind)

    # ------------------------------------------------------------------ #
    # Painting hooks
    # ------------------------------------------------------------------ #

    def _paint_compact(self, p, card):
        if self._assist_paint and self.state != "expanded":
            self._paint_assist(p, card)
            return

        super()._paint_compact(p, card)

        if self.state == "hover":
            rect = self.mic_rect()

            if rect is not None:
                self._paint_mic_button(p, rect.translated(card.topLeft()))

    def _paint_expanded(self, p):
        super()._paint_expanded(p)

        if self.state == "expanded" and not self.stats_view:
            rect = self.mic_rect()

            if rect is not None:
                self._paint_mic_button(p, rect)

    # ------------------------------------------------------------------ #
    # Mic button
    # ------------------------------------------------------------------ #

    def _paint_mic_button(self, p, rect):
        th = self.th
        active = self.assist_state != "off"
        hot = self.hover_item == ("mic", None)
        hud = QColor("#4FD8FF") if self.dark else QColor("#0A8FBF")

        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)

        if active:
            p.setBrush(with_alpha(hud, int(70 + 110 * clamp(self.mic_level))))
            p.drawEllipse(rect)

            colour = QColor(255, 255, 255) if self.dark else QColor(0, 60, 90)

            if self.assist_state in ("speaking", "thinking"):
                glyph = "\uE71A"   # stop
            else:
                glyph = "\uE720"   # microphone

        else:
            if hot:
                p.setBrush(th.tile_hover)
                p.drawEllipse(rect)

            colour = th.text if hot else th.sub
            glyph = "\uE720"

        draw_glyph(p, glyph, rect, 14, colour)

        p.restore()

    # ------------------------------------------------------------------ #
    # Siri-style orb
    # ------------------------------------------------------------------ #

    @staticmethod
    def _blob_path(cx, cy, radius, t, wobble):
        """Closed organic outline: a circle distorted by moving waves."""

        path = QPainterPath()
        steps = 56

        for i in range(steps + 1):
            a = 2 * math.pi * i / steps

            k = (
                math.sin(a * 2 + t * 1.3) * 0.5
                + math.sin(a * 3 - t * 1.7 + 1.0) * 0.3
                + math.sin(a * 5 + t * 2.3 + 2.0) * 0.2
            )

            rad = radius * (1 + wobble * k)
            x = cx + math.cos(a) * rad
            y = cy + math.sin(a) * rad

            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)

        path.closeSubpath()

        return path

    def _render_orb(self, now, level, state):
        dpr = max(1.0, self.devicePixelRatioF())
        side = int(math.ceil(ORB_SIZE * dpr))

        image = QImage(side, side, QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        image.fill(QColor(9, 11, 26))

        # Behaviour per state.
        if state == "speaking":
            level = 0.25 + 0.6 * abs(
                math.sin(now * 9.0) * math.sin(now * 3.1 + 0.7)
            )
            orbit = 1.9
            wobble = 0.20 + 0.30 * level

        elif state == "thinking":
            level = 0.35 + 0.15 * math.sin(now * 5.0)
            orbit = 3.2
            wobble = 0.10

        elif state == "notice":
            level = 0.2
            orbit = 0.6
            wobble = 0.08

        else:  # listening
            level = max(level, 0.12 + 0.06 * math.sin(now * 2.0))
            orbit = 1.0 + 1.6 * level
            wobble = 0.07 + 0.30 * level

        palette = ORB_ALERT if state == "notice" else ORB_COLORS

        cx = cy = ORB_SIZE / 2.0
        radius = ORB_RADIUS * (1.0 + 0.16 * level)

        ip = QPainter(image)
        ip.setRenderHint(QPainter.RenderHint.Antialiasing)
        ip.setPen(Qt.PenStyle.NoPen)

        # Additive light blobs drifting around inside the orb.
        ip.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)

        for colour, phase, speed in palette:
            ang = now * speed * orbit + phase
            gx = cx + math.cos(ang) * radius * 0.55
            gy = cy + math.sin(ang * 1.13) * radius * 0.55

            grad = QRadialGradient(QPointF(gx, gy), radius * 1.15)
            grad.setColorAt(
                0.0,
                QColor(colour.red(), colour.green(), colour.blue(), 235),
            )
            grad.setColorAt(
                1.0,
                QColor(colour.red(), colour.green(), colour.blue(), 0),
            )

            ip.setBrush(QBrush(grad))
            ip.drawEllipse(QPointF(cx, cy), radius * 1.7, radius * 1.7)

        # Soft glass highlight.
        ip.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        gloss = QRadialGradient(
            QPointF(cx - radius * 0.35, cy - radius * 0.45),
            radius * 0.7,
        )
        gloss.setColorAt(0.0, QColor(255, 255, 255, 120))
        gloss.setColorAt(1.0, QColor(255, 255, 255, 0))

        ip.setBrush(QBrush(gloss))
        ip.drawEllipse(QPointF(cx, cy), radius * 1.7, radius * 1.7)

        # Cut away everything outside the wobbling outline.
        blob = self._blob_path(cx, cy, radius, now, wobble)

        outside = QPainterPath()
        outside.addRect(QRectF(0.0, 0.0, side / dpr + 2, side / dpr + 2))
        outside = outside.subtracted(blob)

        ip.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        ip.setBrush(QColor(0, 0, 0, 255))
        ip.drawPath(outside)

        ip.end()

        return image

    def _paint_assist(self, p, card):
        th = self.th
        now = time.perf_counter()
        state = self._assist_draw_state

        hud = QColor("#4FD8FF") if self.dark else QColor("#0A8FBF")

        if state == "notice":
            hud = QColor(th.bad)

        level = clamp(self.mic_level)
        hearing = state == "listening" and level > 0.18

        p.save()
        p.setOpacity(
            p.opacity() * clamp((now - self._assist_shown_at) / 0.25)
        )

        cy = card.top() + min(card.height(), ASSIST_H) / 2
        x0 = card.left() + 16
        centre = QPointF(x0 + 19, cy)

        # Soft coloured glow behind the orb.
        glow_level = level if state == "listening" else 0.45

        glow = QRadialGradient(centre, 34.0)
        glow.setColorAt(0.0, QColor(130, 110, 255, int(40 + 90 * glow_level)))
        glow.setColorAt(1.0, QColor(130, 110, 255, 0))

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(glow))
        p.drawEllipse(centre, 34.0, 34.0)

        # The orb itself.
        orb = self._render_orb(now, level, state)

        p.drawImage(
            QPointF(centre.x() - ORB_SIZE / 2, centre.y() - ORB_SIZE / 2),
            orb,
        )

        # Label: shows whether sound is actually arriving.
        tx = x0 + 46
        avail = ASSIST_W - 62 - 83

        label = "HEARING YOU" if hearing else LABELS.get(state, "STANDBY")

        label_font = QFont(ui_font(9, QFont.Weight.DemiBold))
        label_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.4)

        draw_text(
            p,
            f"GLANCE  \u00b7  {label}",
            tx,
            cy - 6,
            label_font,
            with_alpha(hud, 230),
        )

        # Caption: what Windows is hearing, or what Glance is saying.
        caption = self.assist_caption
        caption_t = self._assist_cap_t

        if (
            state == "listening"
            and self.assist_hyp
            and now - self._hyp_t < 1.8
        ):
            caption = self.assist_hyp
            caption_t = self._hyp_t

        self._draw_caption(
            p,
            caption,
            tx,
            cy + 13,
            avail,
            ui_font(12),
            th.text,
            now - caption_t,
        )

        # Live microphone level meter.
        if state == "listening":
            bx = card.right() - 16 - 28 - 10 - 23
            profile = (0.5, 0.8, 1.0, 0.8, 0.5)

            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(with_alpha(hud, 220 if hearing else 110))

            for i, k in enumerate(profile):
                wob = 0.75 + 0.25 * math.sin(now * 9.0 + i * 1.3)
                h = 3.0 + level * 16.0 * k * wob

                p.drawRoundedRect(
                    QRectF(bx + i * 5, cy - h / 2, 3, h),
                    1.5,
                    1.5,
                )

        # Mic / stop button.
        self._paint_mic_button(
            p,
            QRectF(card.right() - 44, cy - 14, 28, 28),
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