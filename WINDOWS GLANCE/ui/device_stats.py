
import sys
from collections import deque

from PySide6.QtCore import Qt, QThread, QPointF
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication, QWidget, QFrame, QLabel,
    QVBoxLayout, QHBoxLayout, QGridLayout, QProgressBar,
)

from core.system_monitor_worker import SystemMonitorWorker


BACKGROUND = "#101319"
CARD_BACKGROUND = "#191F29"
BORDER = "#293241"
TEXT = "#F1F5F9"
MUTED = "#8995A7"
BLUE = "#55A6FF"
GREEN = "#42D6A4"
PURPLE = "#A78BFA"
ORANGE = "#F5A65B"


def format_bytes(value):
    value = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def format_speed(value):
    return f"{format_bytes(value)}/s"


class HistoryChart(QWidget):
    def __init__(self, color=BLUE, parent=None):
        super().__init__(parent)
        self.color = QColor(color)
        self.values = deque(maxlen=60)
        self.setMinimumHeight(95)

    def add_value(self, value):
        self.values.append(float(value))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        width, height = self.width(), self.height()
        painter.setPen(QPen(QColor("#2B3442"), 1))

        for fraction in (0.25, 0.50, 0.75):
            y = int(height * fraction)
            painter.drawLine(0, y, width, y)

        if len(self.values) < 2:
            painter.end()
            return

        maximum = max(max(self.values), 1.0)
        points = [
            QPointF(
                index / (len(self.values) - 1) * width,
                height - (value / maximum) * (height - 12) - 6,
            )
            for index, value in enumerate(self.values)
        ]

        area = QPainterPath()
        area.moveTo(points[0])
        for point in points[1:]:
            area.lineTo(point)
        area.lineTo(width, height)
        area.lineTo(0, height)
        area.closeSubpath()

        fill = QColor(self.color)
        fill.setAlpha(28)
        painter.fillPath(area, fill)

        line = QPainterPath()
        line.moveTo(points[0])
        for point in points[1:]:
            line.lineTo(point)

        pen = QPen(self.color, 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawPath(line)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.color)
        painter.drawEllipse(points[-1], 3.5, 3.5)
        painter.end()


class MetricCard(QFrame):
    def __init__(self, title, color, show_progress=False, parent=None):
        super().__init__(parent)
        self.color = color
        self.setObjectName("metricCard")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)

        heading = QHBoxLayout()
        indicator = QLabel("●")
        indicator.setStyleSheet(f"color: {color}; font-size: 11px;")

        title_label = QLabel(title)
        title_label.setObjectName("cardTitle")

        heading.addWidget(indicator)
        heading.addWidget(title_label)
        heading.addStretch()
        layout.addLayout(heading)

        self.value_label = QLabel("--")
        self.value_label.setObjectName("metricValue")
        layout.addWidget(self.value_label)

        self.detail_label = QLabel("Waiting for data...")
        self.detail_label.setObjectName("metricDetail")
        layout.addWidget(self.detail_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(7)
        self.progress.setVisible(show_progress)
        self.progress.setStyleSheet(f"""
            QProgressBar {{
                background: #2A3341;
                border: none;
                border-radius: 3px;
            }}
            QProgressBar::chunk {{
                background: {color};
                border-radius: 3px;
            }}
        """)
        layout.addWidget(self.progress)

        self.chart = HistoryChart(color)
        layout.addWidget(self.chart)

        footer = QLabel("LIVE · LAST 60 READINGS")
        footer.setObjectName("cardFooter")
        layout.addWidget(footer)

    def update_card(self, value, detail, chart_value=None, progress=None):
        self.value_label.setText(str(value))
        self.detail_label.setText(str(detail))

        if progress is not None:
            self.progress.setValue(max(0, min(100, int(progress))))

        if chart_value is not None:
            self.chart.add_value(chart_value)


class SystemMonitorView(QWidget):
    """System dashboard. Can use an external worker or run independently."""

    def __init__(self, parent=None, start_monitoring=True):
        super().__init__(parent)

        self.setWindowTitle("Windows Glance | System Monitor")
        self.resize(1000, 720)
        self.setMinimumSize(760, 580)

        self.monitor_thread = None
        self.monitor_worker = None

        self.setStyleSheet(f"""
            QWidget {{
                background-color: {BACKGROUND};
                color: {TEXT};
                font-family: "Segoe UI";
            }}
            QFrame#metricCard {{
                background-color: {CARD_BACKGROUND};
                border: 1px solid {BORDER};
                border-radius: 14px;
            }}
            QLabel#cardTitle {{
                color: {MUTED};
                font-size: 13px;
                font-weight: 600;
            }}
            QLabel#metricValue {{
                color: {TEXT};
                font-size: 30px;
                font-weight: 700;
            }}
            QLabel#metricDetail {{
                color: {MUTED};
                font-size: 12px;
            }}
            QLabel#cardFooter {{
                color: #687589;
                font-size: 9px;
                letter-spacing: 1px;
            }}
            QLabel#pageTitle {{
                font-size: 25px;
                font-weight: 700;
            }}
            QLabel#subtitle {{
                color: {MUTED};
                font-size: 12px;
            }}
            QLabel#status {{
                color: {GREEN};
                font-size: 11px;
                font-weight: 600;
            }}
        """)

        self.build_interface()

        if start_monitoring:
            self.start_monitoring()

    def build_interface(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(20)

        header = QHBoxLayout()
        title_area = QVBoxLayout()

        title = QLabel("System Performance")
        title.setObjectName("pageTitle")

        subtitle = QLabel("Real-time hardware and network monitoring")
        subtitle.setObjectName("subtitle")

        title_area.addWidget(title)
        title_area.addWidget(subtitle)
        header.addLayout(title_area)
        header.addStretch()

        self.status_label = QLabel("● CONNECTING")
        self.status_label.setObjectName("status")
        header.addWidget(self.status_label)
        root.addLayout(header)

        grid = QGridLayout()
        grid.setSpacing(16)

        self.cpu_card = MetricCard("PROCESSOR", BLUE, True)
        self.memory_card = MetricCard("MEMORY", PURPLE, True)
        self.disk_card = MetricCard("STORAGE", ORANGE, True)
        self.network_card = MetricCard("NETWORK", GREEN)

        grid.addWidget(self.cpu_card, 0, 0)
        grid.addWidget(self.memory_card, 0, 1)
        grid.addWidget(self.disk_card, 1, 0)
        grid.addWidget(self.network_card, 1, 1)

        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(0, 1)
        grid.setRowStretch(1, 1)
        root.addLayout(grid, 1)

        footer = QHBoxLayout()
        self.last_update_label = QLabel("Waiting for first system reading...")
        self.last_update_label.setObjectName("subtitle")
        footer.addWidget(self.last_update_label)
        footer.addStretch()

        version = QLabel("WINDOWS GLANCE · PERFORMANCE")
        version.setObjectName("cardFooter")
        footer.addWidget(version)
        root.addLayout(footer)

    def start_monitoring(self):
        if self.monitor_thread is not None:
            return

        self.monitor_thread = QThread(self)
        self.monitor_worker = SystemMonitorWorker(interval=1000)
        self.monitor_worker.moveToThread(self.monitor_thread)

        self.monitor_thread.started.connect(self.monitor_worker.start)
        self.monitor_worker.metrics_ready.connect(self.update_metrics)
        self.monitor_worker.error_occurred.connect(self.show_error)
        self.monitor_worker.finished.connect(self.monitor_thread.quit)
        self.monitor_worker.finished.connect(self.monitor_worker.deleteLater)
        self.monitor_thread.finished.connect(self.monitor_thread.deleteLater)

        self.monitor_thread.start()

    def update_metrics(self, metrics):
        cpu = metrics["cpu"]
        memory = metrics["memory"]
        disk = metrics["disk"]
        network = metrics["network"]

        cpu_usage = float(cpu["usage"])
        memory_usage = float(memory["usage"])
        disk_usage = float(disk["usage"])

        self.cpu_card.update_card(
            f"{cpu_usage:.1f}%",
            "Processor utilisation",
            chart_value=cpu_usage,
            progress=cpu_usage,
        )

        self.memory_card.update_card(
            f"{memory_usage:.1f}%",
            f"{format_bytes(memory['used'])} used of {format_bytes(memory['total'])}",
            chart_value=memory_usage,
            progress=memory_usage,
        )

        self.disk_card.update_card(
            f"{disk_usage:.1f}%",
            f"Read {format_speed(disk['read_speed'])} · Write {format_speed(disk['write_speed'])}",
            chart_value=disk_usage,
            progress=disk_usage,
        )

        download = float(network["download_speed"])
        upload = float(network["upload_speed"])

        self.network_card.update_card(
            f"↓ {format_speed(download)}",
            f"Upload: {format_speed(upload)}",
            chart_value=download,
        )

        self.status_label.setText("● MONITORING LIVE")
        self.status_label.setStyleSheet("")
        self.last_update_label.setText("Metrics refresh every second")

    def show_error(self, message):
        self.status_label.setText("● MONITORING ERROR")
        self.status_label.setStyleSheet(
            "color: #FF7373; font-size: 11px; font-weight: 600;"
        )
        self.last_update_label.setText(str(message))

    def closeEvent(self, event):
        if self.monitor_worker is not None and self.monitor_thread is not None:
            self.monitor_worker.stop_requested.emit()
            self.monitor_thread.quit()
            self.monitor_thread.wait(2000)

        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = SystemMonitorView()
    window.show()
    sys.exit(app.exec())
