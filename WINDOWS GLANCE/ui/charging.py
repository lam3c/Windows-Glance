"""
Windows Glance - charging live activity.

When the charger is plugged in (or pulled out) while the pill is idle, the
pill pops out with a short animation, shows the battery level, then shrinks
back to the idle sliver.

Hooked into engine.py through ChargingMixin.

Test without a charger (PowerShell):
    $env:GLANCE_DEMO = 1; python main.py
"""

import math
import os
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QLinearGradient,
    QPainterPath,
    QPen,
    QPolygonF,
)

from core.common import PILL_H, clamp, draw_text, ui_font, with_alpha
from core.system_worker import read_battery


CHARGE_W = 184          # pill width while the activity is showing
BLOCK_W = 158           # width of the content block inside the pill
CHARGE_SECONDS = 3.4    # how long the activity stays on screen
CHARGE_POLL_MS = 500    # how often the charger state is checked
CHARGE_FRAME_MS = 33    # animation frame interval (~30 fps)


class ChargingMixin:
    """Detects plug / unplug and paints the pill activity."""

    # Class-level defaults so the attributes exist before init_charging().
    charge_activity = None   # active activity (drives the pill size)
    _charge_paint = None     # activity being painted (kept during shrink)
    _charge_stable = None    # last confirmed plugged state
    _charge_pending = None   # state waiting for a second confirming reading

    # ------------------------------------------------------------------ #
    # Setup and detection
    # ------------------------------------------------------------------ #

    def init_charging(self):
        self._charge_poll_timer = QTimer(self)
        self._charge_poll_timer.timeout.connect(self._charge_poll)
        self._charge_poll_timer.start(CHARGE_POLL_MS)

        self._charge_anim_timer = QTimer(self)
        self._charge_anim_timer.setInterval(CHARGE_FRAME_MS)
        self._charge_anim_timer.timeout.connect(self._charge_tick)

        # Demo mode: fake a plug-in, then an unplug, without a charger.
        if os.environ.get("GLANCE_DEMO"):
            QTimer.singleShot(
                2000, lambda: self.start_charge_activity("charging")
            )
            QTimer.singleShot(
                8000, lambda: self.start_charge_activity("unplugged")
            )

    def _charge_poll(self):
        try:
            reading = read_battery()
        except Exception:
            return

        if reading is None:  # desktop PC without a battery
            self._charge_stable = None
            self._charge_pending = None
            return

        if reading != self.battery:
            self.battery = reading
            self.update()

        plugged = bool(reading["plugged"])

        if self._charge_stable is None:
            self._charge_stable = plugged  # first reading: no animation
            return

        if plugged == self._charge_stable:
            self._charge_pending = None
            return

        # State changed: confirm it on a second consecutive reading.
        if self._charge_pending != plugged:
            self._charge_pending = plugged
            return

        self._charge_stable = plugged
        self._charge_pending = None

        self.start_charge_activity("charging" if plugged else "unplugged")

    # ------------------------------------------------------------------ #
    # Activity lifecycle
    # ------------------------------------------------------------------ #

    def charge_size(self):
        return float(CHARGE_W), float(PILL_H)

    def start_charge_activity(self, kind):
        # Only pop out from the quiet idle state.
        if self.state != "idle" or not self.isVisible():
            return

        activity = {"kind": kind, "start": time.perf_counter()}

        self.charge_activity = activity
        self._charge_paint = activity

        self._charge_anim_timer.start()
        self.retarget(480)
        self.update()

    def end_charge_activity(self, immediate=False):
        if self.charge_activity is None and self._charge_paint is None:
            return

        self.charge_activity = None
        self._charge_anim_timer.stop()

        if immediate:
            self._charge_paint = None
            return

        self.retarget(320)
        self.update()

        # Keep painting the content while the pill shrinks.
        QTimer.singleShot(450, self._charge_clear_paint)

    def _charge_clear_paint(self):
        if self.charge_activity is None:
            self._charge_paint = None
            self.update()

    def _charge_tick(self):
        activity = self.charge_activity

        if activity is None:
            self._charge_anim_timer.stop()
            return

        if time.perf_counter() - activity["start"] >= CHARGE_SECONDS:
            self.end_charge_activity()
            return

        self.update()

    # ------------------------------------------------------------------ #
    # Painting
    # ------------------------------------------------------------------ #

    def _paint_compact(self, p, card):
        activity = self._charge_paint

        if activity is not None and self.state == "idle":
            self._paint_charge_activity(p, card, activity)
            return

        super()._paint_compact(p, card)

    def _paint_charge_activity(self, p, card, activity):
        th = self.th
        now = time.perf_counter()
        elapsed = now - activity["start"]
        plugged = activity["kind"] == "charging"

        battery = self.battery or {"percent": 76, "plugged": plugged}
        level = max(0, min(100, int(battery["percent"])))

        grow = 1 - (1 - clamp(elapsed / 0.8)) ** 3   # ease-out count-up
        shown = int(round(level * grow))

        p.save()
        p.setOpacity(p.opacity() * clamp(elapsed / 0.22))

        cy = card.top() + min(card.height(), PILL_H) / 2
        x0 = card.center().x() - BLOCK_W / 2

        # Battery icon.
        self._draw_charge_battery(
            p,
            QRectF(x0, cy - 7, 30, 14),
            level,
            grow,
            plugged,
            now,
        )

        text_x = x0 + 40

        # Pulsing lightning bolt while charging.
        if plugged:
            pulse = 0.5 + 0.5 * math.sin(now * 5.0)

            self._draw_charge_bolt(
                p,
                QRectF(x0 + 38, cy - 8, 9, 16),
                with_alpha(th.good, int(170 + 85 * pulse)),
            )

            text_x = x0 + 56

        # Label and percentage.
        font = ui_font(12, QFont.Weight.DemiBold)
        fm = QFontMetricsF(font)

        base = cy + (fm.ascent() - fm.descent()) / 2

        if plugged:
            label = "Charged" if level >= 100 else "Charging"
            colour = th.good
        elif level <= 20:
            label = "Low battery"
            colour = th.bad
        else:
            label = "On battery"
            colour = th.text

        pct_text = f"{shown}%"
        pct_x = x0 + BLOCK_W - fm.horizontalAdvance(pct_text)
        pct_min_x = x0 + BLOCK_W - fm.horizontalAdvance("100%")

        draw_text(
            p,
            label,
            text_x,
            base,
            font,
            th.text,
            pct_min_x - text_x - 6,
        )

        draw_text(p, pct_text, pct_x, base, font, colour)

        p.restore()

    def _draw_charge_battery(self, p, rect, level, grow, plugged, now):
        th = self.th

        if plugged:
            col = th.good
        elif level <= 20:
            col = th.bad
        else:
            col = th.text

        body = QRectF(
            rect.left(),
            rect.top(),
            rect.width() - 3,
            rect.height(),
        )

        # Outline.
        p.setPen(QPen(with_alpha(col, 200), 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(body.adjusted(0.6, 0.6, -0.6, -0.6), 3.5, 3.5)

        # Terminal nub.
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(
            QRectF(body.right() + 1, rect.center().y() - 2.5, 2, 5),
            1,
            1,
        )

        # Fill grows from empty to the current level.
        inner = body.adjusted(2.2, 2.2, -2.2, -2.2)

        fill = QRectF(
            inner.left(),
            inner.top(),
            max(2.0, inner.width() * (level / 100.0) * grow),
            inner.height(),
        )

        fill_path = QPainterPath()
        fill_path.addRoundedRect(fill, 1.8, 1.8)

        p.fillPath(fill_path, col)

        # Sweeping shine while charging.
        if plugged:
            p.save()

            p.setClipPath(fill_path, Qt.ClipOperation.IntersectClip)

            phase = (now * 0.8) % 1.0
            sx = fill.left() - 10 + phase * (fill.width() + 20)

            shine = QLinearGradient(sx - 8, 0, sx + 8, 0)
            shine.setColorAt(0.0, QColor(255, 255, 255, 0))
            shine.setColorAt(0.5, QColor(255, 255, 255, 120))
            shine.setColorAt(1.0, QColor(255, 255, 255, 0))

            p.fillRect(fill, QBrush(shine))

            p.restore()

    def _draw_charge_bolt(self, p, rect, colour):
        pts = (
            (0.62, 0.00),
            (0.00, 0.58),
            (0.42, 0.58),
            (0.30, 1.00),
            (1.00, 0.38),
            (0.56, 0.38),
        )

        poly = QPolygonF([
            QPointF(
                rect.left() + u * rect.width(),
                rect.top() + v * rect.height(),
            )
            for u, v in pts
        ])

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(colour)
        p.drawPolygon(poly)