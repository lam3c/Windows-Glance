"""Windows Glance - in-widget performance view.
Mixin that draws a live CPU / memory / storage / network dashboard inside the expanded pill.
"""

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainterPath, QPen

from core.common import (
    EXP_W,
    clamp,
    draw_glyph,
    draw_text,
    format_bytes,
    format_rate_short,
    ui_font,
    with_alpha,
)

# Layout constants for the performance view
STATS_HISTORY = 60

STATS_CARD_W = 160
STATS_CARD_H = 108
STATS_CARD_GAP = 8
STATS_VIEW_TOP = 56

STATS_VIEW_H = STATS_VIEW_TOP + 2 * STATS_CARD_H + STATS_CARD_GAP + 16

STATS_BACK = QRectF(12, 12, 32, 32)
STATS_TASKMGR = QRectF(EXP_W - 116, 14, 100, 28)

DEFAULT_STAT_COLORS = ("#4CC9F0", "#7209B7", "#F72585", "#43AA8B")


class StatsViewMixin:
    """Paints the performance view when self.stats_view is True."""

    # Hook into the normal expanded panel
    def _paint_expanded(self, p):
        if getattr(self, "stats_view", False):
            self._paint_stats_view(p)
            return

        super()._paint_expanded(p)

    # Geometry and data
    def stats_card_rect(self, idx):
        col, row = idx % 2, idx // 2
        return QRectF(
            16 + col * (STATS_CARD_W + STATS_CARD_GAP),
            STATS_VIEW_TOP + row * (STATS_CARD_H + STATS_CARD_GAP),
            STATS_CARD_W,
            STATS_CARD_H,
        )

    def _net_peak(self):
        hist = list(getattr(self, "net_history", ()))
        return max(max(hist, default=0.0), 1024 * 1024.0)

    def _stats_card_data(self):
        """Return one list per card: [label, value, detail, history, peak, color]."""
        stat_colors = getattr(self, "STAT_COLORS", DEFAULT_STAT_COLORS)
        colors = [QColor(c) for c in stat_colors]
        m = getattr(self, "metrics", None)

        cards = [
            ["PROCESSOR", "--", "Waiting for data...", self.cpu_history, 100.0],
            ["MEMORY", "--", "Waiting for data...", self.mem_history, 100.0],
            [
                "STORAGE",
                "--",
                "Waiting for data...",
                self.disk_history,
                max(max(list(self.disk_history), default=0.0), 1024 * 1024.0),
            ],
            ["NETWORK", "--", "Waiting for data...", self.net_history, self._net_peak()],
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
                    f"{format_bytes(mem['used'])} of {format_bytes(mem['total'])}",
                ]

                cards[2][1:3] = [
                    f"{float(disk['usage']):.0f}%",
                    f"R {format_rate_short(disk['read_speed'])}/s · "
                    f"W {format_rate_short(disk['write_speed'])}/s",
                ]

                net_down = net.get("download_speed", net.get("recv_speed", 0.0))
                net_up = net.get("upload_speed", net.get("sent_speed", 0.0))
                net_total = float(net_down) + float(net_up)

                cards[3][1:3] = [
                    f"{format_rate_short(net_total)}/s",
                    f"↓ {format_rate_short(net_down)}/s · "
                    f"↑ {format_rate_short(net_up)}/s",
                ]
            except (KeyError, TypeError, ValueError):
                pass

        for card, color in zip(cards, colors):
            card.append(color)

        return cards

    # Drawing
    def _draw_chart(self, p, rect, values, color, peak):
        """Filled live line chart that grows in from the right edge."""
        p.save()
        p.setClipRect(rect, Qt.ClipOperation.IntersectClip)

        p.setPen(QPen(with_alpha(self.th.text, 22), 1))
        mid = rect.top() + rect.height() / 2
        p.drawLine(QPointF(rect.left(), mid), QPointF(rect.right(), mid))

        pts = list(values)
        if len(pts) >= 2:
            peak = max(float(peak), 1e-9)
            step = rect.width() / max(STATS_HISTORY - 1, 1)
            n = len(pts)

            points = []
            for i, v in enumerate(pts):
                x = rect.right() - (n - 1 - i) * step
                y = (
                    rect.bottom()
                    - 2
                    - clamp(float(v) / peak, 0.0, 1.0) * (rect.height() - 4)
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

        # Back button
        back = STATS_BACK
        hov = getattr(self, "hover_item", None) == ("stats_back", None)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(th.tile_hover if hov else th.tile)
        p.drawRoundedRect(back, 7, 7)
        draw_glyph(p, "\uE72B", back, 14, th.text)

        # Optional error banner if metrics failed to collect
        metrics_error = getattr(self, "metrics_error", None)
        if metrics_error:
            draw_text(
                p,
                str(metrics_error),
                16,
                96,
                ui_font(11),
                th.bad,
                EXP_W - 32,
            )

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
            p.drawEllipse(QPointF(rect.left() + 16, rect.top() + 16), 3, 3)

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
                rect.top() + 42,
                ui_font(16, QFont.Weight.DemiBold),
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
                    rect.top() + 74,
                    rect.width() - 24,
                    24,
                ),
                history,
                color,
                peak,
            )