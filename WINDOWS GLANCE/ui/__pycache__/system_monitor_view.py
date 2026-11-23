
import sys
from collections import deque

from PySide6.QtCore import Qt, QThread, QTimer, QPointF
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from core.system_monitor_worker import SystemMonitorWorker


# ---------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------

def format_bytes(value):
    value = float(value)

    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024

    return f"{value:.1f} TB"


def format_speed(value):
    return f"{format_bytes(value)}/s"


# ---------------------------------------------------------
# History chart
# ---------------------------------------------------------

class HistoryChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.values = deque(maxlen=60)
        self.setMinimumHeight(85)

    def add_value(self, value):
        self.values.append(max(0.0, float(value)))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect().adjusted(5, 8, -5, -8)

        if len(self.values) < 2:
            painter.setPen(QColor("#64748b"))
            painter.drawText(
                rect,
                Qt.AlignmentFlag.AlignCenter,
                "Collecting performance data...",
            )
            return

        width = rect.width()
        height = rect.height()

        maximum = max(max(self.values), 1.0)
        points = []

        for index, value in enumerate(self.values):
            x = rect.left() + (
                index / (len(self.values) - 1)
            ) * width

            y = rect.bottom() - (
                value / maximum
            ) * height

            points.append(QPointF(x, y))

        # Baseline
        painter.setPen(QPen(QColor("#263449"), 1))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())

        # Filled area
        area = QPainterPath()
        area.moveTo(points[0])

        for point in points[1:]:
            area.lineTo(point)

        area.lineTo(QPointF(points[-1].x(), rect.bottom()))
        area.lineTo(QPointF(points[0].x(), rect.bottom()))
        area.closeSubpath()

        painter.fillPath(area, QColor(59, 130, 246, 35))

        # Performance line
        line = QPainterPath()
        line.moveTo(points[0])

        for point in points[1:]:
            line.lineTo(point)

        painter.setPen(
            QPen(
                QColor("#60a5fa"),
                2,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        painter.drawPath(line)


# ---------------------------------------------------------
# Individual metric card
# ---------------------------------------------------------

class MetricCard(QFrame):
    def __init__(self, title, accent="#60a5fa", parent=None):
        super().__init__(parent)

        self.setObjectName("metricCard")
        self.accent = accent

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)

        header = QHBoxLayout()

        self.title_label = QLabel(title.upper())
        self.title_label.setObjectName("cardTitle")

        self.status_label = QLabel("LIVE")
        self.status_label.setObjectName("liveLabel")

        header.addWidget(self.title_label)
        header.addStretch()
        header.addWidget(self.status_label)

        layout.addLayout(header)

        self.value_label = QLabel("--")
        self.value_label.setObjectName("metricValue")

        self.detail_label = QLabel("Waiting for data")
        self.detail_label.setObjectName("metricDetail")

        layout.addWidget(self.value_label)
        layout.addWidget(self.detail_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        self.progress.setStyleSheet(
            f"""
            QProgressBar {{
                background: #263449;
                border: none;
                border-radius: 3px;
            }}
            QProgressBar::chunk {{
                background: {accent};
                border-radius: 3px;
            }}
            """
        )

        layout.addWidget(self.progress)

        self.chart = HistoryChart()
        layout.addWidget(self.chart)

    def update_metric(self, value, detail, percentage=None):
        self.value_label.setText(value)
        self.detail_label.setText(detail)

        if percentage is not None:
            self.progress.setValue(
                max(0, min(100, int(percentage)))
            )
        else:
            self.progress.setValue(0)

        self.chart.add_value(
            percentage if percentage is not None else 0
        )


# ---------------------------------------------------------
# Main system monitoring window
# ---------------------------------------------------------

class SystemMonitorView(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Windows Glance | System Statistics")
        self.resize(1050, 760)
        self.setMinimumSize(780, 600)

        self.thread = None
        self.worker = None
        self.closing = False

        self.build_interface()
        self.start_monitoring()

    def build_interface(self):
        central = QWidget()
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(30, 26, 30, 26)
        root.setSpacing(20)

        # Header
        header = QHBoxLayout()

        heading = QVBoxLayout()

        title = QLabel("System Statistics")
        title.setObjectName("pageTitle")

        subtitle = QLabel(
            "Real-time performance and resource monitoring"
        )
        subtitle.setObjectName("subtitle")

        heading.addWidget(title)
        heading.addWidget(subtitle)

        self.connection_label = QLabel("INITIALISING")
        self.connection_label.setObjectName("connectionLabel")

        header.addLayout(heading)
        header.addStretch()
        header.addWidget(self.connection_label)

        root.addLayout(header)

        # Summary
        summary = QHBoxLayout()
        summary.setSpacing(12)

        self.cpu_summary = self.create_summary("PROCESSOR", "--")
        self.ram_summary = self.create_summary("MEMORY", "--")
        self.disk_summary = self.create_summary("STORAGE", "--")

        summary.addWidget(self.cpu_summary)
        summary.addWidget(self.ram_summary)
        summary.addWidget(self.disk_summary)

        root.addLayout(summary)

        # Metric cards
        grid = QGridLayout()
        grid.setSpacing(16)

        self.cpu_card = MetricCard("Processor", "#60a5fa")
        self.memory_card = MetricCard("Memory", "#a78bfa")
        self.disk_card = MetricCard("Storage", "#34d399")
        self.network_card = MetricCard("Network", "#fbbf24")

        grid.addWidget(self.cpu_card, 0, 0)
        grid.addWidget(self.memory_card, 0, 1)
        grid.addWidget(self.disk_card, 1, 0)
        grid.addWidget(self.network_card, 1, 1)

        root.addLayout(grid, 1)

        footer = QLabel(
            "WINDOWS GLANCE  •  LIVE SYSTEM MONITORING  •  "
            "REFRESH INTERVAL: 1 SECOND"
        )
        footer.setObjectName("footer")

        root.addWidget(footer)

        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: #0b1220;
                color: #e5e7eb;
                font-family: "Segoe UI";
            }

            QLabel#pageTitle {
                font-size: 27px;
                font-weight: 700;
                color: #f8fafc;
            }

            QLabel#subtitle {
                font-size: 12px;
                color: #94a3b8;
                margin-top: 3px;
            }

            QLabel#connectionLabel {
                color: #34d399;
                background: #102c28;
                border: 1px solid #1c5548;
                border-radius: 10px;
                padding: 8px 13px;
                font-size: 10px;
                font-weight: 700;
            }

            QFrame#metricCard {
                background: #131e30;
                border: 1px solid #263449;
                border-radius: 15px;
            }

            QLabel#cardTitle {
                color: #94a3b8;
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 1px;
            }

            QLabel#liveLabel {
                color: #34d399;
                font-size: 9px;
                font-weight: 700;
            }

            QLabel#metricValue {
                color: #f8fafc;
                font-size: 29px;
                font-weight: 700;
            }

            QLabel#metricDetail {
                color: #94a3b8;
                font-size: 11px;
            }

            QLabel#summaryCard {
                background: #131e30;
                border: 1px solid #263449;
                border-radius: 10px;
                padding: 13px;
            }

            QLabel#summaryTitle {
                color: #94a3b8;
                font-size: 9px;
                font-weight: 700;
            }

            QLabel#summaryValue {
                color: #f8fafc;
                font-size: 19px;
                font-weight: 700;
            }

            QLabel#footer {
                color: #64748b;
                font-size: 9px;
                letter-spacing: 1px;
            }
            """
        )

    def create_summary(self, title, value):
        frame = QFrame()
        frame.setObjectName("metricCard")

        layout = QVBoxLayout(frame)
        layout.setContentsMargins(15, 12, 15, 12)
        layout.setSpacing(5)

        title_label = QLabel(title)
        title_label.setObjectName("summaryTitle")

        value_label = QLabel(value)
        value_label.setObjectName("summaryValue")

        layout.addWidget(title_label)
        layout.addWidget(value_label)

        frame.value_label = value_label
        return frame

    # -----------------------------------------------------
    # Worker thread
    # -----------------------------------------------------

    def start_monitoring(self):
        self.thread = QThread(self)
        self.worker = SystemMonitorWorker(interval=1000)

        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.start)

        self.worker.metrics_ready.connect(self.update_dashboard)
        self.worker.error_occurred.connect(self.show_error)

        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)

        self.thread.finished.connect(self.thread.deleteLater)

        self.thread.start()

    # -----------------------------------------------------
    # Live dashboard updates
    # -----------------------------------------------------

    def update_dashboard(self, metrics):
        cpu = metrics["cpu"]
        memory = metrics["memory"]
        disk = metrics["disk"]
        network = metrics["network"]

        cpu_usage = float(cpu["usage"])
        memory_usage = float(memory["usage"])
        disk_usage = float(disk["usage"])

        self.cpu_summary.value_label.setText(
            f"{cpu_usage:.1f}%"
        )
        self.ram_summary.value_label.setText(
            f"{memory_usage:.1f}%"
        )
        self.disk_summary.value_label.setText(
            f"{disk_usage:.1f}%"
        )

        self.cpu_card.update_metric(
            f"{cpu_usage:.1f}%",
            "Current processor utilisation",
            cpu_usage,
        )

        self.memory_card.update_metric(
            f"{memory_usage:.1f}%",
            f"{format_bytes(memory['used'])} used of "
            f"{format_bytes(memory['total'])}",
            memory_usage,
        )

        self.disk_card.update_metric(
            f"{disk_usage:.1f}%",
            f"{format_bytes(disk['used'])} used of "
            f"{format_bytes(disk['total'])}  •  "
            f"Read {format_speed(disk['read_speed'])}  •  "
            f"Write {format_speed(disk['write_speed'])}",
            disk_usage,
        )

        download = network["download_speed"]
        upload = network["upload_speed"]

        self.network_card.update_metric(
            format_speed(download),
            f"Download  •  {format_speed(download)}",
        )

        self.network_card.chart.add_value(
            min(download / (1024 * 1024) * 100, 100)
        )

        self.network_card.detail_label.setText(
            f"↓ {format_speed(download)}     "
            f"↑ {format_speed(upload)}"
        )

        self.connection_label.setText("● LIVE")

    def show_error(self, message):
        self.connection_label.setText("MONITORING ERROR")
        self.connection_label.setStyleSheet(
            "color: #f87171; background: #351b25; "
            "border: 1px solid #71313e; "
            "border-radius: 10px; padding: 8px 13px;"
        )
        self.statusBar().showMessage(message)

    # -----------------------------------------------------
    # Safe shutdown
    # -----------------------------------------------------

    def closeEvent(self, event):
        if self.closing:
            event.accept()
            return

        self.closing = True

        if self.worker is not None and self.thread is not None:
            self.worker.stop_requested.emit()

            if not self.thread.wait(3000):
                self.thread.quit()
                self.thread.wait(2000)

        event.accept()


# ---------------------------------------------------------
# Application entry point
# ---------------------------------------------------------

def main():
    app = QApplication.instance()

    if app is None:
        app = QApplication(sys.argv)

    window = SystemMonitorView()
    window.show()

    if QApplication.instance() is app:
        sys.exit(app.exec())


if __name__ == "__main__":
    main()
