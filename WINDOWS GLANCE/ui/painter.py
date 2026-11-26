"""
Windows Glance - UI Painter

Handles all visual rendering for the Windows Glance widget.
"""

import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)

from core.common import (
    CTRL,
    EXP_W,
    HOVER_MEDIA_H,
    IDLE_H,
    MEDIA_Y,
    PILL_H,
    R_COMPACT,
    R_IDLE,
    R_PANEL,
    STAT_GAP,
    STAT_H,
    STAT_W,
    STATS_Y,
    TILE_GAP,
    TILE_H,
    TILE_W,
    TILES_Y,
    clamp,
    contrast,
    date_text,
    draw_glyph,
    draw_text,
    draw_weather,
    fmt_time,
    format_rate_short,
    time_text,
    ui_font,
    with_alpha,
    wx_text,
    STATS_BACK,
    STATS_CARD_GAP,
    STATS_CARD_H,
    STATS_CARD_W,
    STATS_HISTORY,
    STATS_TASKMGR,
    STATS_VIEW_TOP,
    format_bytes,
)


_EMPTY_STATS = [
    ("CPU", "--", 0.0),
    ("RAM", "--", 0.0),
    ("DISK", "--", 0.0),
    ("NET", "--", 0.0),
]


class PainterMixin:

    # ------------------------------------------------------------------ #
    # Performance view (drawn inside the pill)
    # ------------------------------------------------------------------ #

    STAT_COLORS = ("#55A6FF", "#A78BFA", "#F5A65B", "#42D6A4")

    def stats_card_rect(self, idx):
        col, row = idx % 2, idx // 2

        return QRectF(
            16 + col * (STATS_CARD_W + STATS_CARD_GAP),
            STATS_VIEW_TOP + row * (STATS_CARD_H + STATS_CARD_GAP),
            STATS_CARD_W,
            STATS_CARD_H,
        )

    def _stats_card_data(self):
        """Return [label, value, detail, history, peak, colour] per card."""

        colors = [QColor(c) for c in self.STAT_COLORS]
        m = self.metrics

        cards = [
            ["PROCESSOR", "--", "Waiting for data...",
             self.cpu_history, 100.0],
            ["MEMORY", "--", "Waiting for data...",
             self.mem_history, 100.0],
            ["STORAGE", "--", "Waiting for data...",
             self.disk_history,
             max(max(self.disk_history, default=0.0), 1024 * 1024.0)],
            ["NETWORK", "--", "Waiting for data...",
             self.net_history, self.net_peak()],
        ]

        if m:
            try:
                cpu = float(m["cpu"]["usage"])
                mem = m["memory"]
                disk = m["disk"]
                net = m["network"]

                cards[0][1:3] = [f"{cpu:.0f}%", "Processor load"]

                cards[1][1:3] = [
                    f"{float(mem['usage']):.0f}%",
                    f"{format_bytes(mem['used'])} of "
                    f"{format_bytes(mem['total'])}",
                ]

                cards[2][1:3] = [
                    f"{float(disk['usage']):.0f}%",
                    f"R {format_rate_short(disk['read_speed'])}/s  "
                    f"W {format_rate_short(disk['write_speed'])}/s",
                ]

                cards[3][1:3] = [
                    f"{format_bytes(net['download_speed'])}/s",
                    f"Upload {format_bytes(net['upload_speed'])}/s",
                ]

            except (KeyError, TypeError, ValueError):
                pass

        for card, color in zip(cards, colors):
            card.append(color)

        return cards

    def _draw_chart(self, p, rect, values, color, peak):
        """Filled live line chart, growing in from the right edge."""

        p.save()
        p.setClipRect(rect, Qt.ClipOperation.IntersectClip)

        p.setPen(QPen(with_alpha(self.th.text, 22), 1))
        mid = rect.top() + rect.height() / 2

        p.drawLine(QPointF(rect.left(), mid), QPointF(rect.right(), mid))

        pts = list(values)

        if len(pts) >= 2:
            peak = max(float(peak), 1e-9)
            step = rect.width() / (STATS_HISTORY - 1)
            n = len(pts)

            points = []

            for i, v in enumerate(pts):
                x = rect.right() - (n - 1 - i) * step
                y = (
                    rect.bottom() - 2
                    - clamp(v / peak) * (rect.height() - 4)
                )
                points.append(QPointF(x, y))

            area = QPainterPath()
            area.moveTo(points[0].x(), rect.bottom())

            for pt in points:
                area.lineTo(pt)

            area.lineTo(points[-1].x(), rect.bottom())
            area.closeSubpath()

            p.fillPath(area, with_alpha(color, 40))

            line = QPainterPath()
            line.moveTo(points[0])

            for pt in points[1:]:
                line.lineTo(pt)

            pen = QPen(color, 1.6)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)

            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(line)

        p.restore()

    def _paint_stats_view(self, p):
        th = self.th

        # Back button.
        back = STATS_BACK
        hov = self.hover_item == ("stats_back", None)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(th.tile_hover if hov else th.tile)
        p.drawRoundedRect(back, 7, 7)

        draw_glyph(
            p,
            "\uE72B",
            QRectF(back.left() + 6, back.top() + 6, 20, 20),
            14,
            th.text,
        )

        draw_text(
            p,
            "System Performance",
            52,
            33,
            ui_font(14, QFont.Weight.DemiBold),
            th.text,
        )

        # Task Manager shortcut.
        tm = STATS_TASKMGR
        hov = self.hover_item == ("stats_taskmgr", None)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(th.tile_hover if hov else th.tile)
        p.drawRoundedRect(tm, 7, 7)

        draw_text(
            p,
            "Task Manager",
            tm.left() + 10,
            tm.top() + 18,
            ui_font(10, QFont.Weight.DemiBold),
            th.text,
            tm.width() - 16,
        )

        if not self.monitor_available:
            draw_text(
                p,
                "System stats unavailable - run: pip install psutil",
                16,
                96,
                ui_font(11),
                th.sub,
                EXP_W - 32,
            )
            return

        if self.metrics is None and self.metrics_error:
            draw_text(
                p,
                self.metrics_error,
                16,
                96,
                ui_font(11),
                th.bad,
                EXP_W - 32,
            )
            return

        for idx, card in enumerate(self._stats_card_data()):
            label, value, detail, history, peak, color = card
            rect = self.stats_card_rect(idx)

            path = QPainterPath()
            path.addRoundedRect(rect, 8, 8)

            p.fillPath(path, th.tile)

            p.setPen(QPen(th.stroke, 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)

            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawEllipse(
                QPointF(rect.left() + 16, rect.top() + 16),
                3,
                3,
            )

            draw_text(
                p,
                label,
                rect.left() + 26,
                rect.top() + 20,
                ui_font(10, QFont.Weight.DemiBold),
                th.sub,
            )

            draw_text(
                p,
                value,
                rect.left() + 12,
                rect.top() + 46,
                ui_font(18, QFont.Weight.DemiBold),
                th.text,
                rect.width() - 24,
            )

            draw_text(
                p,
                detail,
                rect.left() + 12,
                rect.top() + 62,
                ui_font(10),
                th.sub,
                rect.width() - 24,
            )

            self._draw_chart(
                p,
                QRectF(
                    rect.left() + 12,
                    rect.top() + 70,
                    rect.width() - 24,
                    28,
                ),
                history,
                color,
                peak,
            )

    # ------------------------------------------------------------------ #
    # Geometry helpers
    # ------------------------------------------------------------------ #

    def vol_rects(self):
        """Mute / volume-down / volume-up buttons (card coordinates)."""

        y = MEDIA_Y + 38 if self.media else MEDIA_Y - 4

        return {
            "mute": QRectF(EXP_W - 120, y, 32, 32),
            "vdn": QRectF(EXP_W - 84, y, 32, 32),
            "vup": QRectF(EXP_W - 48, y, 32, 32),
        }

    def stat_rect(self, idx):
        return QRectF(
            16 + idx * (STAT_W + STAT_GAP),
            STATS_Y,
            STAT_W,
            STAT_H,
        )

    def stat_cells(self):
        """Return [(label, value text, bar fraction)] for the stats row."""

        m = self.metrics

        if not m:
            return list(_EMPTY_STATS)

        try:
            cpu = float(m["cpu"]["usage"])
            ram = float(m["memory"]["usage"])
            disk = float(m["disk"]["usage"])
            down = float(m["network"]["download_speed"])

        except (KeyError, TypeError, ValueError):
            return list(_EMPTY_STATS)

        return [
            ("CPU", f"{cpu:.0f}%", clamp(cpu / 100.0)),
            ("RAM", f"{ram:.0f}%", clamp(ram / 100.0)),
            ("DISK", f"{disk:.0f}%", clamp(disk / 100.0)),
            (
                "NET",
                "\u2193" + format_rate_short(down),
                clamp(down / self.net_peak()),
            ),
        ]

    # ------------------------------------------------------------------ #
    # Main paint routine
    # ------------------------------------------------------------------ #

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
            + (R_PANEL - R_COMPACT) * open_,
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

        # Fluent-style border: lighter on top, softer at the bottom.
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
            r - 0.5,
        )

        p.drawPath(outline)
        p.end()

    # ------------------------------------------------------------------ #
    # Small drawing helpers
    # ------------------------------------------------------------------ #

    def _draw_battery(self, p, rect, level, plugged):
        th = self.th

        col = th.good if plugged else (th.bad if level <= 20 else th.text)

        body = QRectF(
            rect.left(),
            rect.top(),
            rect.width() - 2.5,
            rect.height(),
        )

        p.setPen(QPen(with_alpha(col, 210), 1.1))
        p.setBrush(Qt.BrushStyle.NoBrush)

        p.drawRoundedRect(
            body.adjusted(0.55, 0.55, -0.55, -0.55),
            2.5,
            2.5,
        )

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)

        p.drawRoundedRect(
            QRectF(body.right() + 0.8, rect.center().y() - 2, 1.6, 4),
            0.8,
            0.8,
        )

        inner = body.adjusted(2, 2, -2, -2)

        p.drawRoundedRect(
            QRectF(
                inner.left(),
                inner.top(),
                max(1.5, inner.width() * level / 100),
                inner.height(),
            ),
            1.2,
            1.2,
        )

        if plugged:
            pts = [
                (0.56, 0.12),
                (0.32, 0.56),
                (0.50, 0.56),
                (0.44, 0.90),
                (0.70, 0.42),
                (0.52, 0.42),
            ]

            poly = QPolygonF([
                QPointF(
                    body.left() + x * body.width(),
                    body.top() + y * body.height(),
                )
                for x, y in pts
            ])

            p.setBrush(QColor(th.bg.red(), th.bg.green(), th.bg.blue()))
            p.drawPolygon(poly)

    def _draw_eq(self, p, x, cy, bar_h=10.0, min_h=4.0, step=4.5, w=2.5):
        t = time.perf_counter()

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self.accent)

        for i in range(4):
            h = min_h + bar_h * (0.5 + 0.5 * math.sin(t * 6.5 + i * 1.7))

            p.drawRoundedRect(
                QRectF(x + i * step, cy - h / 2, w, h),
                w / 2,
                w / 2,
            )

    # ------------------------------------------------------------------ #
    # Idle and compact states
    # ------------------------------------------------------------------ #

    def _paint_idle(self, p, card):
        if self.is_playing():
            self._draw_eq(
                p,
                card.center().x() - 8.5,
                card.center().y(),
                3.5,
                2.0,
                5.0,
                2.5,
            )

    def _status_row(self, p, x0, cy, base):
        """Draw the status row, or just measure its width when p is None."""

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
                p.fillRect(QRectF(x, cy - 7, 1, 14), with_alpha(th.text, 45))

            x += 11

            if draw:
                draw_weather(
                    p,
                    self.weather["code"],
                    self.weather["is_day"],
                    QRectF(x, cy - 8, 16, 16),
                    th.dark,
                )

            x += 22

            t = f'{self.weather["temp"]}°'

            if draw:
                draw_text(p, t, x, base, f, th.text)

            x += fm.horizontalAdvance(t)

        x += 10

        if draw:
            p.fillRect(QRectF(x, cy - 7, 1, 14), with_alpha(th.text, 45))

        x += 11

        wifi_active = (
            self.wifi_on if self.wifi_on is not None else self.wifi_connected
        )

        if draw:
            draw_glyph(
                p,
                "\uE701",
                QRectF(x, cy - 9, 14, 18),
                14,
                th.text if wifi_active else th.sub,
            )

        x += 14 + 10

        if draw:
            draw_glyph(
                p,
                "\uE702",
                QRectF(x, cy - 9, 14, 18),
                14,
                th.text if self.bt else th.sub,
            )

        x += 14

        if self.battery:
            x += 12

            if draw:
                self._draw_battery(
                    p,
                    QRectF(x, cy - 5.5, 22, 11),
                    self.battery["percent"],
                    self.battery["plugged"],
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

        self._status_row(p, card.center().x() - row_w / 2, cy, base)

        if self.media:
            self._paint_hover_media(p, card)

    def _paint_hover_media(self, p, card):
        th, m = self.th, self.media

        top = card.top() + PILL_H

        p.fillRect(
            QRectF(card.left() + 12, top - 1, card.width() - 24, 1),
            with_alpha(th.text, 22),
        )

        art = QRectF(card.left() + 12, top + 6, 32, 32)

        path = QPainterPath()
        path.addRoundedRect(art, 5, 5)

        if self.art:
            p.save()
            p.setClipPath(path, Qt.ClipOperation.IntersectClip)
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
            avail,
        )

        draw_text(
            p,
            m["artist"] or "Unknown artist",
            card.left() + 54,
            top + 34,
            ui_font(11),
            th.sub,
            avail,
        )

        glyphs = {
            "prev": ("\uE892", 13),
            "play": ("\uE769" if m["playing"] else "\uE768", 15),
            "next": ("\uE893", 13),
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
            frac = clamp(self.cur_pos() / m["duration"])

            x0 = card.left() + 12
            wd = card.width() - 24
            y = card.top() + PILL_H + HOVER_MEDIA_H - 4

            p.fillRect(QRectF(x0, y, wd, 2), with_alpha(th.text, 36))
            p.fillRect(QRectF(x0, y, max(2.0, wd * frac), 2), self.accent)

    # ------------------------------------------------------------------ #
    # Expanded panel
    # ------------------------------------------------------------------ #

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
            th.text,
        )

        draw_text(
            p,
            ds,
            EXP_W - 16 - QFontMetricsF(df).horizontalAdvance(ds),
            56,
            df,
            th.sub,
        )

        if w:
            draw_weather(
                p,
                w["code"],
                w["is_day"],
                QRectF(16, 14, 40, 40),
                th.dark,
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
                th.sub,
            )

            draw_text(
                p,
                f'{wx_text(w["code"])} · {w["city"]}',
                66,
                56,
                ui_font(12),
                th.sub,
                200,
            )

        else:
            draw_text(p, self.wx_msg, 16, 42, ui_font(12), th.sub)

    def _draw_tile(self, p, idx, label, status, active, glyph):
        th = self.th

        rect = QRectF(
            16 + idx * (TILE_W + TILE_GAP),
            TILES_Y,
            TILE_W,
            TILE_H,
        )

        hov = self.hover_item == ("tile", idx) and idx != 2

        if active:
            fill = self.accent.lighter(110) if hov else self.accent
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
                QRectF(rect.left() + 12, rect.top() + 8, 20, 20),
                16,
                fg,
            )

        elif self.battery:
            self._draw_battery(
                p,
                QRectF(rect.left() + 12, rect.top() + 12, 26, 13),
                self.battery["percent"],
                self.battery["plugged"],
            )

        else:
            draw_glyph(
                p,
                "\uE83E",
                QRectF(rect.left() + 12, rect.top() + 8, 20, 20),
                16,
                fg,
            )

        draw_text(
            p,
            label,
            rect.left() + 12,
            rect.top() + 42,
            ui_font(12, QFont.Weight.DemiBold),
            fg,
            TILE_W - 20,
        )

        draw_text(
            p,
            status,
            rect.left() + 12,
            rect.top() + 56,
            ui_font(11),
            sub,
            TILE_W - 20,
        )

    def _paint_stats(self, p):
        th = self.th

        if not self.monitor_available:
            draw_text(
                p,
                "System stats unavailable - run: pip install psutil",
                16,
                STATS_Y + 32,
                ui_font(11),
                th.sub,
                EXP_W - 32,
            )
            return

        if self.metrics is None and self.metrics_error:
            draw_text(
                p,
                self.metrics_error,
                16,
                STATS_Y + 32,
                ui_font(11),
                th.bad,
                EXP_W - 32,
            )
            return

        for idx, (label, value, frac) in enumerate(self.stat_cells()):
            rect = self.stat_rect(idx)
            hov = self.hover_item == ("stat", idx)

            path = QPainterPath()
            path.addRoundedRect(rect, 6, 6)

            p.fillPath(path, th.tile_hover if hov else th.tile)

            p.setPen(QPen(th.stroke, 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)

            draw_text(
                p,
                label,
                rect.left() + 10,
                rect.top() + 16,
                ui_font(10, QFont.Weight.DemiBold),
                th.sub,
            )

            draw_text(
                p,
                value,
                rect.left() + 10,
                rect.top() + 35,
                ui_font(14, QFont.Weight.DemiBold),
                th.text,
                STAT_W - 16,
            )

            track = QRectF(
                rect.left() + 10,
                rect.bottom() - 10,
                STAT_W - 20,
                4,
            )

            tp = QPainterPath()
            tp.addRoundedRect(track, 2, 2)
            p.fillPath(tp, with_alpha(th.text, 36))

            if frac > 0:
                fp = QPainterPath()
                fp.addRoundedRect(
                    QRectF(
                        track.left(),
                        track.top(),
                        max(4.0, track.width() * frac),
                        4,
                    ),
                    2,
                    2,
                )

                busy = idx < 3 and frac > 0.9

                p.fillPath(fp, th.bad if busy else self.accent)

    def _paint_expanded(self, p):
        if self.stats_view:
            self._paint_statsa_view(p)
        th = self.th

        self._paint_weather_strip(p)

        # Notification bell.
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
            th.text,
        )

        unread = self.notifications.unread_count()

        if unread:
            badge_text = str(unread) if unread < 10 else "9+"

            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self.accent)
            p.drawEllipse(QRectF(EXP_W - 20, 7, 16, 16))

            draw_text(
                p,
                badge_text,
                EXP_W - 20,
                18,
                ui_font(8, QFont.Weight.Bold),
                contrast(self.accent),
                16,
            )

        bt_text = {True: "On", False: "Off", None: "Unavailable"}[self.bt]

        if self.battery:
            b = self.battery

            batt_text = (
                f'{b["percent"]}% · '
                f'{"Plugged in" if b["plugged"] else "On battery"}'
            )

        else:
            batt_text = "No battery"

        wifi_active = (
            self.wifi_on if self.wifi_on is not None else self.wifi_connected
        )

        self._draw_tile(p, 0, "Wi-Fi", self.wifi_text, wifi_active, "\uE701")
        self._draw_tile(p, 1, "Bluetooth", bt_text, self.bt is True, "\uE702")
        self._draw_tile(p, 2, "Battery", batt_text, False, None)

        self._paint_stats(p)

        if self.media:
            self._paint_media(p)

        else:
            draw_glyph(
                p,
                "\uE8D6",
                QRectF(16, MEDIA_Y - 2, 18, 18),
                14,
                th.sub,
            )

            draw_text(
                p,
                "Nothing is playing",
                40,
                MEDIA_Y + 11,
                ui_font(12),
                th.sub,
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
                draw_glyph(p, "\uE74F", r, 15, th.text)

            else:
                p.setFont(ui_font(19))
                p.setPen(th.text)

                p.drawText(
                    r,
                    Qt.AlignmentFlag.AlignCenter,
                    "\u2212" if name == "vdn" else "+",
                )

    def _paint_media(self, p):
        th, m, y0 = self.th, self.media, MEDIA_Y

        art = QRectF(16, y0, 64, 64)

        path = QPainterPath()
        path.addRoundedRect(art, 6, 6)

        if self.art:
            p.save()
            p.setClipPath(path, Qt.ClipOperation.IntersectClip)
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
            EXP_W - 108,
        )

        draw_text(
            p,
            m["artist"] or "Unknown artist",
            92,
            y0 + 33,
            ui_font(12),
            th.sub,
            EXP_W - 108,
        )

        glyphs = {
            "prev": ("\uE892", 14),
            "play": ("\uE769" if m["playing"] else "\uE768", 17),
            "next": ("\uE893", 14),
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

            track = QRectF(16, y0 + 80, EXP_W - 32, 4)

            tp = QPainterPath()
            tp.addRoundedRect(track, 2, 2)
            p.fillPath(tp, with_alpha(th.text, 40))

            if frac > 0:
                fp = QPainterPath()
                fp.addRoundedRect(
                    QRectF(
                        track.left(),
                        track.top(),
                        max(4.0, track.width() * frac),
                        4,
                    ),
                    2,
                    2,
                )

                p.fillPath(fp, self.accent)

            if self.hover_item == ("seek", None):
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(self.accent)

                p.drawEllipse(
                    QPointF(
                        track.left() + track.width() * frac,
                        track.center().y(),
                    ),
                    5,
                    5,
                )

            f = ui_font(11)

            draw_text(p, fmt_time(pos), 16, y0 + 102, f, th.sub)

            end = fmt_time(m["duration"])

            draw_text(
                p,
                end,
                EXP_W - 16 - QFontMetricsF(f).horizontalAdvance(end),
                y0 + 102,
                f,
                th.sub,
            )