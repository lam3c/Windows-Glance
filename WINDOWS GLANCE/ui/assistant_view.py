"""
Windows Glance - the "Glance" voice assistant.

Off until you press the microphone button (hover pill or expanded panel).
Then a small pill shows a glowing, fluid orb that reacts to your voice while
Glance listens, swirls while it thinks and pulses while it speaks. Say one
command; Glance answers out loud and closes. Click the orb to cancel.

The microphone is only open while the orb is listening.

Hooked into engine.py through AssistantMixin.

Test without a microphone (PowerShell):
    $env:GLANCE_SAY = "what is the battery"; python main.py
"""

import math
import os
import threading
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QBrush,
    QColor,
    QImage,
    QPainter,
    QPainterPath,
    QRadialGradient,
)

from core.commands import CommandRouter
from core.common import (
    PILL_H,
    clamp,
    draw_glyph,
    set_click_through,
    with_alpha,
)
from core.voice import VoiceIO


COMMAND_CONFIDENCE = 0.65    # lower = accepts shakier commands
LISTEN_SECONDS = 12.0        # how long the microphone stays open
CHIME = True                 # short beep when the microphone goes live

ASSIST_W = 84                # the AI pill is just a small capsule
ASSIST_H = 58
MIC_SLOT = 52                # extra width of the hover pill for the mic button

ORB_SIZE = 64                # orb canvas, logical pixels
ORB_RADIUS = 16.0

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


def _beep():
    try:
        import winsound

        winsound.Beep(1100, 70)
    except Exception:
        pass


class AssistantMixin:
    """Voice assistant state machine, mic button and glowing orb."""

    # Class-level defaults so attributes exist before init_assistant().
    assistant_active = False     # pill is showing the orb
    _assist_paint = False        # keep painting while the pill shrinks
    assist_state = "off"         # off, listening, thinking, speaking, error
    _assist_draw_state = "listening"
    mic_live = False             # the microphone is actually open
    mic_level = 0.0
    _assist_shown_at = 0.0
    _session_until = 0.0
    _error_until = 0.0
    hold_open_until = 0.0

    # ------------------------------------------------------------------ #
    # Setup
    # ------------------------------------------------------------------ #

    def init_assistant(self):
        self.assist_router = CommandRouter(self)

        self.voice = VoiceIO(self)
        self.voice.heard.connect(self._on_heard)
        self.voice.level.connect(self._on_level)
        self.voice.listening.connect(self._on_mic_live)
        self.voice.speech_done.connect(self._on_speech_done)
        self.voice.status.connect(self._on_voice_status)

        self._assist_timer = QTimer(self)
        self._assist_timer.setInterval(33)
        self._assist_timer.timeout.connect(self._assist_tick)

        # Helpers start armed, but the microphone stays closed until clicked.
        self.voice.start(sorted(self.assist_router.map))

        test = os.environ.get("GLANCE_SAY")

        if test:
            QTimer.singleShot(5000, lambda: self._run_command(test.lower()))

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

        # While the orb is showing, the whole pill is the cancel button.
        if self.assistant_active:
            return QRectF(0, 0, card.width(), card.height())

        if self.state == "expanded":
            if self.stats_view:
                return None

            mute = self.vol_rects()["mute"]

            return QRectF(mute.left() - 36, mute.top(), 32, 32)

        if self.state == "hover":
            return QRectF(card.width() - 38, PILL_H / 2 - 13, 26, 26)

        return None

    def toggle_listening(self):
        """Mic button: start one listening session, or cancel the current one."""

        if self.assist_state != "off":
            self._cancel_session()
            return

        if self.state == "expanded":
            self.set_state("hover")

        self.mic_live = False
        self.mic_level = 0.0
        self._session_until = time.perf_counter() + LISTEN_SECONDS

        self._set_assist("listening")
        self._show_assist()
        self.voice.start_listening()

    def _cancel_session(self):
        if self.assist_state == "listening":
            self.voice.stop_listening()

        if self.voice.speaking:
            self.voice.stop_speaking()

        self._end_assist()

    # ------------------------------------------------------------------ #
    # Voice events
    # ------------------------------------------------------------------ #

    def _on_mic_live(self):
        if self.assist_state == "listening":
            self.mic_live = True

            if CHIME:
                threading.Thread(target=_beep, daemon=True).start()

    def _on_level(self, value):
        if self.assist_state == "listening" and self.mic_live:
            self.mic_level = max(self.mic_level, clamp(value / 55.0))

    def _on_heard(self, text, conf):
        t = " ".join(text.lower().split())

        print(f"[Glance] heard '{t}' ({conf:.2f})")

        if self.assist_state != "listening" or not t:
            return

        if conf < COMMAND_CONFIDENCE:
            return

        self._run_command(t)

    def _on_voice_status(self, message, is_error):
        print(f"[Glance voice] {message}")

        # Only show a problem if the user is actually using the assistant.
        if is_error and self.assist_state == "listening":
            self.voice.stop_listening()
            self._flash_error()

    # ------------------------------------------------------------------ #
    # Conversation flow
    # ------------------------------------------------------------------ #

    def _run_command(self, cmd):
        cmd = cmd.strip()

        if not cmd:
            return

        # Close the microphone as soon as a command has been heard.
        if self.assist_state == "listening":
            self.voice.stop_listening()

        self._set_assist("thinking")
        self._show_assist()

        reply, _keep_listening = self.assist_router.handle(cmd)

        QTimer.singleShot(250, lambda r=reply: self._deliver(r))

    def _deliver(self, reply):
        if self.assist_state != "thinking":
            return  # cancelled by clicking the orb

        self._set_assist("speaking")
        self.voice.say(reply)

    def _on_speech_done(self):
        if self.assist_state == "speaking":
            self._end_assist()

    def _flash_error(self):
        self._error_until = time.perf_counter() + 1.8
        self._set_assist("error")
        self._show_assist()

    def _set_assist(self, state):
        self.assist_state = state
        self._assist_draw_state = state

        if not self._assist_timer.isActive():
            self._assist_timer.start()

        self.update()

    def _end_assist(self):
        self.assist_state = "off"
        self.mic_live = False
        self.mic_level = 0.0
        self._assist_timer.stop()
        self.hide_assistant()

    def _assist_tick(self):
        now = time.perf_counter()
        state = self.assist_state

        if state == "off":
            self._assist_timer.stop()
            return

        if state == "listening" and now >= self._session_until:
            self.voice.stop_listening()

            if self.mic_live:
                self._end_assist()      # heard nothing: just close quietly
            else:
                self._flash_error()     # the microphone never came on

            return

        if state == "error" and now >= self._error_until:
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

        # The pill must accept clicks so the orb can cancel.
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

        # Keep painting the orb while the pill shrinks.
        QTimer.singleShot(450, self._assist_clear_paint)

    def _assist_clear_paint(self):
        if not self.assistant_active:
            self._assist_paint = False
            set_click_through(self, self.state == "idle")
            self.update()

    def start_charge_activity(self, kind):
        # Do not interrupt a running AI session with the charging animation.
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
            p.setBrush(with_alpha(hud, 110))
            p.drawEllipse(rect)

            colour = QColor(255, 255, 255) if self.dark else QColor(0, 60, 90)

            if self.assist_state in ("thinking", "speaking"):
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
    # Glowing orb
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

    def _orb_params(self, now, level, state, live):
        """Return (level, orbit speed, edge wobble, brightness) for a state."""

        if state == "speaking":
            lv = 0.25 + 0.6 * abs(
                math.sin(now * 9.0) * math.sin(now * 3.1 + 0.7)
            )
            return lv, 1.9, 0.20 + 0.30 * lv, 1.0

        if state == "thinking":
            lv = 0.35 + 0.15 * math.sin(now * 5.0)
            return lv, 3.2, 0.10, 1.0

        if state == "error":
            return 0.2, 0.6, 0.08, 1.0

        if live:
            lv = max(level, 0.14 + 0.06 * math.sin(now * 2.0))
            return lv, 1.0 + 1.6 * lv, 0.07 + 0.30 * lv, 1.0

        # Waiting for the microphone to come on: calm and dim.
        return 0.06, 0.5, 0.05, 0.45

    def _render_orb(self, now, params, state):
        level, orbit, wobble, bright = params

        dpr = max(1.0, self.devicePixelRatioF())
        side = int(math.ceil(ORB_SIZE * dpr))

        image = QImage(side, side, QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        image.fill(QColor(9, 11, 26))

        palette = ORB_ALERT if state == "error" else ORB_COLORS

        cx = cy = ORB_SIZE / 2.0
        radius = ORB_RADIUS * (1.0 + 0.16 * level)
        alpha = int(235 * bright)

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
                QColor(colour.red(), colour.green(), colour.blue(), alpha),
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
        gloss.setColorAt(0.0, QColor(255, 255, 255, int(120 * bright)))
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
        now = time.perf_counter()
        state = self._assist_draw_state

        params = self._orb_params(
            now,
            clamp(self.mic_level),
            state,
            self.mic_live,
        )
        glow_level = params[0]

        p.save()
        p.setOpacity(
            p.opacity() * clamp((now - self._assist_shown_at) / 0.25)
        )

        centre = QPointF(
            card.center().x(),
            card.top() + min(card.height(), ASSIST_H) / 2,
        )

        # Soft halo behind the orb: this is the "glow".
        halo = QColor(255, 90, 90) if state == "error" else QColor(120, 130, 255)

        glow = QRadialGradient(centre, 40.0)
        glow.setColorAt(
            0.0,
            QColor(halo.red(), halo.green(), halo.blue(), int(30 + 100 * glow_level)),
        )
        glow.setColorAt(
            1.0,
            QColor(halo.red(), halo.green(), halo.blue(), 0),
        )

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(glow))
        p.drawEllipse(centre, 40.0, 40.0)

        # The orb itself.
        orb = self._render_orb(now, params, state)

        p.drawImage(
            QPointF(centre.x() - ORB_SIZE / 2, centre.y() - ORB_SIZE / 2),
            orb,
        )

        p.restore()