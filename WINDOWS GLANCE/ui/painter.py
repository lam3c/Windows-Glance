
"""
Windows Glance - UI Painter

Handles visual rendering for the Windows Glance interface.
"""

import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor, QBrush, QFont, QFontMetricsF,
    QLinearGradient, QPainter, QPainterPath,
    QPen, QPolygonF
)

from core.engine import (
    clamp, fmt_time, ui_font, draw_glyph, draw_text,
    with_alpha, draw_weather, time_text, date_text,
    wx_text, contrast,
    IDLE_H, PILL_H, EXP_W, HOVER_MEDIA_H,
    MEDIA_Y, TILES_Y, TILE_W, TILE_H, TILE_GAP,
    R_IDLE, R_COMPACT, R_PANEL, CTRL
)


class PainterMixin:
    def vol_rects(self):
        """Mute / volume-down / volume-up buttons (card coordinates)."""
        y = MEDIA_Y + 38 if self.media else MEDIA_Y - 4
        return {
            "mute": QRectF(EXP_W - 120, y, 32, 32),
            "vdn": QRectF(EXP_W - 84, y, 32, 32),
            "vup": QRectF(EXP_W - 48, y, 32, 32),
        }

    def _draw_shadow(self, p, card, r):
        p.setPen(Qt.PenStyle.NoPen)
        steps = 9
        for i in range(steps - 1, -1, -1):
            grow = i * 1.2
            a = int(self.th.shadow * (1 - i / steps) ** 1.6)
            if a <= 0:
                continue
            p.setBrush(QColor(0, 0, 0, a))
            rr = card.adjusted(-grow, -grow + 2, grow, grow + 4)
            p.drawRoundedRect(rr, r + grow, r + grow)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.TextAntialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
        )

        th, card = self.th, self._card
        hh, eh = self.hover_height(), self.exp_height()

        show = clamp((card.height() - IDLE_H) / (PILL_H - IDLE_H))
        open_ = clamp((card.height() - hh) / (eh - hh))
        wprog = clamp((card.width() - 100) / (EXP_W - 100))

        r = min(
            card.height() / 2,
            R_IDLE
            + (R_COMPACT - R_IDLE) * show
            + (R_PANEL - R_COMPACT) * open_
        )

        base = 0.45 + 0.55 * show

        p.setOpacity(show)
        self._draw_shadow(p, card, r)

        p.setOpacity(base)

        path = QPainterPath()
        path.addRoundedRect(card, r, r)
        p.fillPath(path, th.bg)

        sheen = QLinearGradient(card.topLeft(), card.bottomLeft())
        sheen.setColorAt(0.0, th.sheen)
        sheen.setColorAt(0.6, QColor(255, 255, 255, 0))
        p.fillPath(path, QBrush(sheen))

        if self.state == "hover":
            p.fillPath(path, th.hover_overlay)

        p.setClipPath(path)

        ia = clamp(1 - show * 2.2)
        if ia > 0:
            p.setOpacity(ia * 0.9)
            self._paint_idle(p, card)

        ca = clamp((show - 0.55) / 0.45) * clamp(1 - open_ * 3.2)
        if ca > 0:
            p.setOpacity(ca)
            self._paint_compact(p, card)

        ea = clamp((min(open_, wprog) - 0.55) / 0.45)
        if ea > 0:
            p.setOpacity(ea)
            p.save()
            p.translate(card.topLeft())
            self._paint_expanded(p)
            p.restore()

        p.setClipping(False)

        # Fluent-style border: lighter on top, softer at the bottom
        p.setOpacity(base)

        grad = QLinearGradient(card.topLeft(), card.bottomLeft())
        grad.setColorAt(0.0, th.border_hi)
        grad.setColorAt(1.0, th.border_lo)

        p.setPen(QPen(QBrush(grad), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)

        outline = QPainterPath()
        outline.addRoundedRect(
            card.adjusted(0.5, 0.5, -0.5, -0.5),
            r - 0.5,
            r - 0.5
        )

        p.drawPath(outline)
        p.end()

    def _draw_battery(self, p, rect, level, plugged):
        th = self.th

        col = th.good if plugged else (
            th.bad if level <= 20 else th.text
        )

        body = QRectF(
            rect.left(),
            rect.top(),
            rect.width() - 2.5,
            rect.height()
        )

        p.setPen(QPen(with_alpha(col, 210), 1.1))
        p.setBrush(Qt.BrushStyle.NoBrush)

        p.drawRoundedRect(
            body.adjusted(0.55, 0.55, -0.55, -0.55),
            2.5,
            2.5
        )

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)

        p.drawRoundedRect(
            QRectF(
                body.right() + 0.8,
                rect.center().y() - 2,
                1.6,
                4
            ),
            0.8,
            0.8
        )

        inner = body.adjusted(2, 2, -2, -2)

        p.drawRoundedRect(
            QRectF(
                inner.left(),
                inner.top(),
                max(1.5, inner.width() * level / 100),
                inner.height()
            ),
            1.2,
            1.2
        )

        if plugged:
            b = body

            pts = [
                (0.56, 0.12),
                (0.32, 0.56),
                (0.50, 0.56),
                (0.44, 0.90),
                (0.70, 0.42),
                (0.52, 0.42)
            ]

            poly = QPolygonF([
                QPointF(
                    b.left() + x * b.width(),
                    b.top() + y * b.height()
                )
                for x, y in pts
            ])

            p.setBrush(
                QColor(
                    th.bg.red(),
                    th.bg.green(),
                    th.bg.blue()
                )
            )

            p.drawPolygon(poly)

    def _draw_eq(
        self,
        p,
        x,
        cy,
        bar_h=10.0,
        min_h=4.0,
        step=4.5,
        w=2.5
    ):
        t = time.perf_counter()

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self.accent)

        for i in range(4):
            h = min_h + bar_h * (
                0.5 + 0.5 * math.sin(t * 6.5 + i * 1.7)
            )

            p.drawRoundedRect(
                QRectF(
                    x + i * step,
                    cy - h / 2,
                    w,
                    h
                ),
                w / 2,
                w / 2
            )

    def _paint_idle(self, p, card):
        if self.is_playing():
            self._draw_eq(
                p,
                card.center().x() - 8.5,
                card.center().y(),
                3.5,
                2.0,
                5.0,
                2.5
            )

    def _status_row(self, p, x0, cy, base):
        """Draws the status row or measures its width."""
        th = self.th

        f_t = ui_font(12, QFont.Weight.DemiBold)
        f = ui_font(12)

        fmb = QFontMetricsF(f_t)
        fm = QFontMetricsF(f)

        draw = p is not None
        x = x0

        tt = time_text()

        if draw:
            draw_text(p, tt, x, base, f_t, th.text)

        x += fmb.horizontalAdvance(tt)

        if self.weather:
            x += 10

            if draw:
                p.fillRect(
                    QRectF(x, cy - 7, 1, 14),
                    with_alpha(th.text, 45)
                )

            x += 11

            if draw:
                draw_weather(
                    p,
                    self.weather["code"],
                    self.weather["is_day"],
                    QRectF(x, cy - 8, 16, 16),
                    th.dark
                )

            x += 22

            t = f'{self.weather["temp"]}°'

            if draw:
                draw_text(p, t, x, base, f, th.text)

            x += fm.horizontalAdvance(t)

        x += 10

        if draw:
            p.fillRect(
                QRectF(x, cy - 7, 1, 14),
                with_alpha(th.text, 45)
            )

        x += 11

        wifi_active = (
            self.wifi_on
            if self.wifi_on is not None
            else self.wifi_connected
        )

        if draw:
            draw_glyph(
                p,
                "\uE701",
                QRectF(x, cy - 9, 14, 18),
                14,
                th.text if wifi_active else th.sub
            )

        x += 14 + 10

        if draw:
            draw_glyph(
                p,
                "\uE702",
                QRectF(x, cy - 9, 14, 18),
                14,
                th.text if self.bt else th.sub
            )

        x += 14

        if self.battery:
            x += 12

            if draw:
                self._draw_battery(
                    p,
                    QRectF(x, cy - 5.5, 22, 11),
                    self.battery["percent"],
                    self.battery["plugged"]
                )

            x += 22 + 5

            pct = f'{self.battery["percent"]}%'

            if draw:
                draw_text(p, pct, x, base, f, th.text)

            x += fm.horizontalAdvance("100%")

        return x - x0

    def _paint_compact(self, p, card):
        fm = QFontMetricsF(ui_font(12))

        cy = card.top() + min(card.height(), PILL_H) / 2
        base = cy + (fm.ascent() - fm.descent()) / 2

        row_w = self._status_row(None, 0, 0, 0)

        self._status_row(
            p,
            card.center().x() - row_w / 2,
            cy,
            base
        )

        if self.media:
            self._paint_hover_media(p, card)

    def _paint_hover_media(self, p, card):
        th, m = self.th, self.media

        top = card.top() + PILL_H

        p.fillRect(
            QRectF(
                card.left() + 12,
                top - 1,
                card.width() - 24,
                1
            ),
            with_alpha(th.text, 22)
        )

        art = QRectF(
            card.left() + 12,
            top + 6,
            32,
            32
        )

        path = QPainterPath()
        path.addRoundedRect(art, 5, 5)

        if self.art:
            p.save()
            p.setClipPath(
                path,
                Qt.ClipOperation.IntersectClip
            )
            p.drawPixmap(art, self.art, self._art_src)
            p.restore()
        else:
            p.fillPath(path, th.tile_hover)
            draw_glyph(p, "\uE8D6", art, 15, th.sub)

        avail = card.width() - 54 - 112

        draw_text(
            p,
            m["title"],
            card.left() + 54,
            top + 19,
            ui_font(12, QFont.Weight.DemiBold),
            th.text,
            avail
        )

        draw_text(
            p,
            m["artist"] or "Unknown artist",
            card.left() + 54,
            top + 34,
            ui_font(11),
            th.sub,
            avail
        )

        glyphs = {
            "prev": ("\uE892", 13),
            "play": (
                "\uE769" if m["playing"] else "\uE768",
                15
            ),
            "next": ("\uE893", 13)
        }

        for name, rel in self.hover_ctrls(card.width()).items():
            rect = rel.translated(card.topLeft())

            if self.hover_item == ("btn", name):
                bp = QPainterPath()
                bp.addRoundedRect(rect, 6, 6)
                p.fillPath(bp, th.tile_hover)

            g, px = glyphs[name]
            draw_glyph(p, g, rect, px, th.text)

        if m["duration"]:
            frac = clamp(
                self.cur_pos() / m["duration"]
            )

            x0 = card.left() + 12
            wd = card.width() - 24

            y = (
                card.top()
                + PILL_H
                + HOVER_MEDIA_H
                - 4
            )

            p.fillRect(
                QRectF(x0, y, wd, 2),
                with_alpha(th.text, 36)
            )

            p.fillRect(
                QRectF(
                    x0,
                    y,
                    max(2.0, wd * frac),
                    2
                ),
                self.accent
            )

    def _paint_weather_strip(self, p):
        th, w = self.th, self.weather

        tf = ui_font(24, QFont.Weight.DemiBold)
        df = ui_font(11)

        tt, ds = time_text(), date_text()

        draw_text(
            p,
            tt,
            EXP_W - 66 - QFontMetricsF(tf).horizontalAdvance(tt),
            38,
            tf,
            th.text
        )

        draw_text(
            p,
            ds,
            EXP_W - 16 - QFontMetricsF(df).horizontalAdvance(ds),
            56,
            df,
            th.sub
        )

        if w:
            draw_weather(
                p,
                w["code"],
                w["is_day"],
                QRectF(16, 14, 40, 40),
                th.dark
            )

            big = ui_font(26, QFont.Weight.DemiBold)
            t = f'{w["temp"]}°'

            draw_text(p, t, 66, 38, big, th.text)

            bw = QFontMetricsF(big).horizontalAdvance(t)

            draw_text(
                p,
                f'H {w["hi"]}°  L {w["lo"]}°',
                66 + bw + 8,
                37,
                df,
                th.sub
            )

            draw_text(
                p,
                f'{wx_text(w["code"])} · {w["city"]}',
                66,
                56,
                ui_font(12),
                th.sub,
                200
            )
        else:
            draw_text(
                p,
                self.wx_msg,
                16,
                42,
                ui_font(12),
                th.sub
            )

    def _draw_tile(self, p, idx, label, status, active, glyph):
        th = self.th

        rect = QRectF(
            16 + idx * (TILE_W + TILE_GAP),
            TILES_Y,
            TILE_W,
            TILE_H
        )

        hov = (
            self.hover_item == ("tile", idx)
            and idx != 2
        )

        if active:
            fill = (
                self.accent.lighter(110)
                if hov
                else self.accent
            )

            fg = contrast(self.accent)
            sub = with_alpha(fg, 205)
        else:
            fill = th.tile_hover if hov else th.tile
            fg, sub = th.text, th.sub

        path = QPainterPath()
        path.addRoundedRect(rect, 6, 6)

        p.fillPath(path, fill)

        if not active:
            p.setPen(QPen(th.stroke, 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)

        if glyph:
            draw_glyph(
                p,
                glyph,
                QRectF(
                    rect.left() + 12,
                    rect.top() + 8,
                    20,
                    20
                ),
                16,
                fg
            )
        elif self.battery:
            self._draw_battery(
                p,
                QRectF(
                    rect.left() + 12,
                    rect.top() + 12,
                    26,
                    13
                ),
                self.battery["percent"],
                self.battery["plugged"]
            )
        else:
            draw_glyph(
                p,
                "\uE83E",
                QRectF(
                    rect.left() + 12,
                    rect.top() + 8,
                    20,
                    20
                ),
                16,
                fg
            )

        draw_text(
            p,
            label,
            rect.left() + 12,
            rect.top() + 42,
            ui_font(12, QFont.Weight.DemiBold),
            fg,
            TILE_W - 20
        )

        draw_text(
            p,
            status,
            rect.left() + 12,
            rect.top() + 56,
            ui_font(11),
            sub,
            TILE_W - 20
        )

    def _paint_notifications(self, p):
        th = self.th

        # Header
        draw_glyph(
            p,
            "\uE72B",
            QRectF(17, 17, 22, 22),
            16,
            th.text
        )

        draw_text(
            p,
            "Notifications",
            48,
            31,
            ui_font(14, QFont.Weight.DemiBold),
            th.text
        )

        # Header buttons
        buttons = [
            (QRectF(205, 12, 76, 32), "Read all"),
            (QRectF(285, 12, 64, 32), "Clear"),
        ]

        for rect, label in buttons:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(th.tile_hover)
            p.drawRoundedRect(rect, 7, 7)

            draw_text(
                p,
                label,
                rect.left() + 5,
                rect.top() + 20,
                ui_font(10, QFont.Weight.DemiBold),
                th.text,
                rect.width() - 10
            )

        # Back button
        back_rect = QRectF(12, 12, 32, 32)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(th.tile_hover)
        p.drawRoundedRect(back_rect, 7, 7)

        draw_glyph(
            p,
            "\uE72B",
            QRectF(18, 18, 20, 20),
            14,
            th.text
        )

        # Notification list
        items = self.notifications.get_all()[:4]

        if not items:
            draw_glyph(
                p,
                "\uE7F4",
                QRectF(154, 83, 52, 52),
                34,
                th.sub
            )

            draw_text(
                p,
                "You're all caught up",
                90,
                151,
                ui_font(12, QFont.Weight.DemiBold),
                th.sub,
                180
            )

            return

        for i, item in enumerate(items):
            y = 62 + i * 30
            rect = QRectF(12, y, 336, 28)

            # Row background
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(th.tile_hover)
            p.drawRoundedRect(rect, 6, 6)

            # Unread indicator
            if item.get("unread", False):
                p.setBrush(self.accent)
                p.drawEllipse(
                    QRectF(19, y + 10, 6, 6)
                )

            # Notification title
            title = str(
                item.get("title", "Notification")
            )

            message = str(
                item.get("message", "")
            ).strip()

            source = str(
                item.get("source", "Windows Glance")
            )

            draw_text(
                p,
                title,
                31,
                y + 12,
                ui_font(10, QFont.Weight.DemiBold),
                th.text,
                270
            )

            # Source and message preview
            preview = source

            if message:
                preview += " · " + message

            draw_text(
                p,
                preview,
                31,
                y + 24,
                ui_font(9),
                th.sub,
                270
            )

            # Dismiss control
            dismiss_rect = QRectF(310, y, 38, 28)

            if self.hover_item == (
                "notification_dismiss",
                item["id"]
            ):
                p.setBrush(with_alpha(th.text, 25))
                p.drawRoundedRect(dismiss_rect, 5, 5)

            draw_glyph(
                p,
                "\uE711",
                QRectF(319, y + 5, 18, 18),
                11,
                th.sub
            )

    def _paint_expanded(self, p):
        # Show notification centre instead of the normal panel
        if self.notification_view:
            self._paint_notifications(p)
            return

        th = self.th

        self._paint_weather_strip(p)

        # Notification bell
        bell_rect = QRectF(EXP_W - 44, 8, 36, 34)

        if self.hover_item == ("notifications", None):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(th.tile_hover)
            p.drawRoundedRect(bell_rect, 8, 8)

        draw_glyph(
            p,
            "\uE7F4",
            QRectF(EXP_W - 48, 12, 24, 24),
            17,
            th.text
        )

        # Unread notification badge
        unread = self.notifications.unread_count()

        if unread:
            badge_text = (
                str(unread)
                if unread < 10
                else "9+"
            )

            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self.accent)

            p.drawEllipse(
                QRectF(EXP_W - 20, 7, 16, 16)
            )

            draw_text(
                p,
                badge_text,
                EXP_W - 20,
                18,
                ui_font(8, QFont.Weight.Bold),
                contrast(self.accent),
                16
            )

        bt_text = {
            True: "On",
            False: "Off",
            None: "Unavailable"
        }[self.bt]

        if self.battery:
            b = self.battery

            batt_text = (
                f'{b["percent"]}% · '
                f'{"Plugged in" if b["plugged"] else "On battery"}'
            )
        else:
            batt_text = "No battery"

        wifi_active = (
            self.wifi_on
            if self.wifi_on is not None
            else self.wifi_connected
        )

        self._draw_tile(
            p,
            0,
            "Wi-Fi",
            self.wifi_text,
            wifi_active,
            "\uE701"
        )

        self._draw_tile(
            p,
            1,
            "Bluetooth",
            bt_text,
            self.bt is True,
            "\uE702"
        )

        self._draw_tile(
            p,
            2,
            "Battery",
            batt_text,
            False,
            None
        )

        if self.media:
            self._paint_media(p)
        else:
            draw_glyph(
                p,
                "\uE8D6",
                QRectF(16, MEDIA_Y - 2, 18, 18),
                14,
                th.sub
            )

            draw_text(
                p,
                "Nothing is playing",
                40,
                MEDIA_Y + 11,
                ui_font(12),
                th.sub
            )

        self._paint_volume(p)

    def _paint_volume(self, p):
        th = self.th

        for name, r in self.vol_rects().items():
            if self.hover_item == ("vol", name):
                bp = QPainterPath()
                bp.addRoundedRect(r, 6, 6)
                p.fillPath(bp, th.tile_hover)

            if name == "mute":
                draw_glyph(
                    p,
                    "\uE74F",
                    r,
                    15,
                    th.text
                )
            else:
                p.setFont(ui_font(19))
                p.setPen(th.text)

                p.drawText(
                    r,
                    Qt.AlignmentFlag.AlignCenter,
                    "\u2212" if name == "vdn" else "+"
                )

    def _paint_media(self, p):
        th, m, y0 = self.th, self.media, MEDIA_Y

        art = QRectF(16, y0, 64, 64)

        path = QPainterPath()
        path.addRoundedRect(art, 6, 6)

        if self.art:
            p.save()

            p.setClipPath(
                path,
                Qt.ClipOperation.IntersectClip
            )

            p.drawPixmap(art, self.art, self._art_src)
            p.restore()

            p.setPen(QPen(th.stroke, 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        else:
            p.fillPath(path, th.tile_hover)
            draw_glyph(p, "\uE8D6", art, 24, th.sub)

        draw_text(
            p,
            m["title"],
            92,
            y0 + 16,
            ui_font(14, QFont.Weight.DemiBold),
            th.text,
            EXP_W - 108
        )

        draw_text(
            p,
            m["artist"] or "Unknown artist",
            92,
            y0 + 33,
            ui_font(12),
            th.sub,
            EXP_W - 108
        )

        glyphs = {
            "prev": ("\uE892", 14),
            "play": (
                "\uE769" if m["playing"] else "\uE768",
                17
            ),
            "next": ("\uE893", 14)
        }

        for name, rect in CTRL.items():
            if self.hover_item == ("btn", name):
                bp = QPainterPath()
                bp.addRoundedRect(rect, 6, 6)
                p.fillPath(bp, th.tile_hover)

            g, px = glyphs[name]
            draw_glyph(p, g, rect, px, th.text)

        if m["duration"]:
            pos = self.cur_pos()
            frac = clamp(pos / m["duration"])

            track = QRectF(
                16,
                y0 + 80,
                EXP_W - 32,
                4
            )

            tp = QPainterPath()
            tp.addRoundedRect(track, 2, 2)

            p.fillPath(
                tp,
                with_alpha(th.text, 40)
            )

            if frac > 0:
                fp = QPainterPath()

                fp.addRoundedRect(
                    QRectF(
                        track.left(),
                        track.top(),
                        max(4, track.width() * frac),
                        4
                    ),
                    2,
                    2
                )

                p.fillPath(fp, self.accent)

            if self.hover_item == ("seek", None):
                # Scrub handle on hover
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(self.accent)

                p.drawEllipse(
                    QPointF(
                        track.left() + track.width() * frac,
                        track.center().y()
                    ),
                    5,
                    5
                )

            f = ui_font(11)

            draw_text(
                p,
                fmt_time(pos),
                16,
                y0 + 102,
                f,
                th.sub
            )

            end = fmt_time(m["duration"])

            draw_text(
                p,
                end,
                EXP_W - 60 -
                QFontMetricsF(tf).horizontalAdvance(tt),
                y0 + 102,
                f,
                th.sub
            )
