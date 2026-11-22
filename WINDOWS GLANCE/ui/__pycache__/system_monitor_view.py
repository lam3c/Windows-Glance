from collections import deque

from PySide6.QtCore import Qt, QThread
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QFrame,
    QLabel,
    QProgressBar,
)

from core.system_monitor_worker import SystemMonitorWorker


# =========================================================
# THEME
# =========================================================

BACKGROUND = "#0B1220"
CARD_BACKGROUND = "#121A27"
CARD_BORDER = "#26364D"

PRIMARY_TEXT = "#E5EDF7"
SECONDARY_TEXT = "#8293AC"

ACCENT = "#22D3EE"
GREEN = "#34D399"
BLUE = "#60A5FA"
ORANGE = "#FBBF24"


# =========================================================
# METRIC CARD
# =========================================================

class MetricCard(QFrame):
    """A reusable card for displaying a system metric."""

    def __init__(self, title, colour=ACCENT, parent=None):
        super().__init__(parent)

        self.colour = colour

        self.setObjectName("MetricCard")
        self.setMinimumHeight(145)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        self.title_label = QLabel(title)
        self.title_label.setStyleSheet(
            f"color: {SECONDARY_TEXT}; font-size: 12px;"
        )

        self.value_label = QLabel("0%")
        self.value_label.setStyleSheet(
            f"color: {PRIMARY_TEXT}; "
            "font-size: 27px; font-weight: 600;"
        )

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(7)

        self.progress.setStyleSheet(f"""
            QProgressBar {{
                background-color: #263447;
                border: none;
                border-radius: 3px;
            }}

            QProgressBar::chunk {{
                background-color: {self.colour};
                border-radius: 3px;
            }}
        """)

        self.detail_label = QLabel("Waiting for data...")
        self.detail_label.setStyleSheet(
            f"color: {SECONDARY_TEXT}; font-size: 11px;"
        )

        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)
        layout.addWidget(self.progress)
        layout.addWidget(self.detail_label)

        self.setStyleSheet(f"""
            QFrame#MetricCard {{
                background-color: {CARD_BACKGROUND};
                border: 1px solid {CARD_BORDER};
                border-radius: 12px;
            }}
        """)

    def update_metric(self, value, detail="", suffix="%"):
        """Update the card's value, progress and supporting text."""

        numeric_value = max(0, min(100, int(round(value))))

        self.value_label.setText(f"{numeric_value}{suffix}")
        self.progress.setValue(numeric_value)
        self.detail_label.setText(detail)


# =========================================================
# LIVE HISTORY GRAPH
# =========================================================

class LiveGraph(QWidget):
    """Displays a scrolling history of metric readings."""

    def __init__(self, title, colour=ACCENT, parent=None):
        super().__init__(parent)

        self.title = title
        self.colour = QColor(colour)
        self.values = deque([0.0] * 60, maxlen=60)

        self.setMinimumHeight(190)

    def add_value(self, value):
        """Append a new reading and repaint the graph."""

        value = max(0.0, min(100.0, float(value)))

        self.values.append(value)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)

        painter.setRenderHint(
            QPainter.RenderHint.Antialiasing
        )

        rect = self.rect()

        # Card background.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(CARD_BACKGROUND))
        painter.drawRoundedRect(rect, 12, 12)

        # Card border.
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(CARD_BORDER), 1))
        painter.drawRoundedRect(
            rect.adjusted(0, 0, -1, -1),
            12,
            12,
        )

        # Graph heading.
        painter.setPen(QColor(PRIMARY_TEXT))
        painter.drawText(16, 27, self.title)

        left = 16
        right = rect.width() - 16
        top = 45
        bottom = rect.height() - 18

        width = right - left
        height = bottom - top

        if width <= 0 or height <= 0:
            painter.end()
            return

        # Horizontal grid lines.
        painter.setPen(QPen(QColor("#263447"), 1))

        for index in range(5):
            y = top + height * index / 4

            painter.drawLine(
                int(left),
                int(y),
                int(right),
                int(y),
            )

        # Convert readings into graph coordinates.
        points = []

        for index, value in enumerate(self.values):
            x = left + (
                index / (len(self.values) - 1)
            ) * width

            y = bottom - (value / 100.0) * height

            points.append((x, y))

        # Draw the graph line.
        painter.setPen(
            QPen(
                self.colour,
                2,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )

        for index in range(1, len(points)):
            x1, y1 = points[index - 1]
            x2, y2 = points[index]

            painter.drawLine(
                int(x1),
                int(y1),
                int(x2),
                int(y2),
            )

        painter.end()


# =========================================================
# SYSTEM MONITOR WINDOW
# =========================================================

class SystemMonitorView(QWidget):
    """Main graphical system monitoring interface."""

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle("Windows Glance - System Monitor")
        self.resize(900, 680)

        self.thread = None
        self.worker = None
        self._closing = False

        self.build_interface()
        self.start_monitoring()

    # -----------------------------------------------------
    # INTERFACE CONSTRUCTION
    # -----------------------------------------------------

    def build_interface(self):
        self.setStyleSheet(f"""
            QWidget {{
                background-color: {BACKGROUND};
                color: {PRIMARY_TEXT};
                font-family: "Segoe UI";
            }}

            QLabel {{
                background: transparent;
            }}
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(24, 22, 24, 22)
        main_layout.setSpacing(18)

        # Header.
        header_layout = QHBoxLayout()

        title = QLabel("System Monitor")
        title.setStyleSheet(
            f"font-size: 25px; "
            f"font-weight: 700; "
            f"color: {PRIMARY_TEXT};"
        )

        self.status_label = QLabel("INITIALISING")
        self.status_label.setStyleSheet(
            f"color: {ORANGE}; "
            "font-size: 11px; font-weight: 600;"
        )

        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(self.status_label)

        main_layout.addLayout(header_layout)

        subtitle = QLabel(
            "Real-time performance and resource utilisation"
        )
        subtitle.setStyleSheet(
            f"color: {SECONDARY_TEXT}; font-size: 12px;"
        )

        main_layout.addWidget(subtitle)

        # Metric cards.
        cards_layout = QGridLayout()
        cards_layout.setSpacing(14)

        self.cpu_card = MetricCard(
            "CPU UTILISATION",
            ACCENT,
        )

        self.memory_card = MetricCard(
            "MEMORY UTILISATION",
            BLUE,
        )

        self.disk_card = MetricCard(
            "DISK UTILISATION",
            GREEN,
        )

        self.network_card = MetricCard(
            "NETWORK ACTIVITY",
            ORANGE,
        )

        cards_layout.addWidget(self.cpu_card, 0, 0)
        cards_layout.addWidget(self.memory_card, 0, 1)
        cards_layout.addWidget(self.disk_card, 1, 0)
        cards_layout.addWidget(self.network_card, 1, 1)

        main_layout.addLayout(cards_layout)

        # History graphs.
        graphs_layout = QGridLayout()
        graphs_layout.setSpacing(14)

        self.cpu_graph = LiveGraph(
            "CPU HISTORY",
            ACCENT,
        )

        self.memory_graph = LiveGraph(
            "MEMORY HISTORY",
            BLUE,
        )

        graphs_layout.addWidget(self.cpu_graph, 0, 0)
        graphs_layout.addWidget(self.memory_graph, 0, 1)

        main_layout.addLayout(graphs_layout)

        # Live activity panel.
        activity_frame = QFrame()
        activity_frame.setObjectName("ActivityFrame")

        activity_layout = QVBoxLayout(activity_frame)
        activity_layout.setContentsMargins(16, 14, 16, 14)
        activity_layout.setSpacing(10)

        activity_title = QLabel("LIVE ACTIVITY")
        activity_title.setStyleSheet(
            f"color: {SECONDARY_TEXT}; "
            "font-size: 11px; font-weight: 600;"
        )

        self.disk_activity_label = QLabel(
            "Disk Read: 0 B/s    |    Disk Write: 0 B/s"
        )

        self.network_activity_label = QLabel(
            "Download: 0 B/s    |    Upload: 0 B/s"
        )

        for label in (
            self.disk_activity_label,
            self.network_activity_label,
        ):
            label.setStyleSheet(
                f"color: {PRIMARY_TEXT}; font-size: 12px;"
            )

        activity_layout.addWidget(activity_title)
        activity_layout.addWidget(self.disk_activity_label)
        activity_layout.addWidget(self.network_activity_label)

        activity_frame.setStyleSheet(f"""
            QFrame#ActivityFrame {{
                background-color: {CARD_BACKGROUND};
                border: 1px solid {CARD_BORDER};
                border-radius: 12px;
            }}
        """)

        main_layout.addWidget(activity_frame)
        main_layout.addStretch()

        footer = QLabel(
            "WINDOWS GLANCE  |  PERFORMANCE MONITOR"
        )

        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        footer.setStyleSheet(
            f"color: {SECONDARY_TEXT}; font-size: 10px;"
        )

        main_layout.addWidget(footer)

    # -----------------------------------------------------
    # WORKER THREAD
    # -----------------------------------------------------

    def start_monitoring(self):
        """Create and start the background monitoring thread."""

        self.thread = QThread(self)
        self.worker = SystemMonitorWorker(interval=1000)

        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.start)

        self.worker.metrics_ready.connect(
            self.refresh_metrics
        )

        self.worker.error_occurred.connect(
            self.handle_monitor_error
        )

        self.worker.finished.connect(
            self.thread.quit
        )

        self.worker.finished.connect(
            self.worker.deleteLater
        )

        self.thread.finished.connect(
            self.thread.deleteLater
        )

        self.thread.finished.connect(
            self.monitoring_stopped
        )

        self.thread.start()

    # -----------------------------------------------------
    # VALUE FORMATTERS
    # -----------------------------------------------------

    @staticmethod
    def format_bytes(value):
        """Convert a byte count into a readable value."""

        value = float(value)

        units = ["B", "KB", "MB", "GB", "TB"]

        for unit in units:
            if value < 1024:
                return f"{value:.2f} {unit}"

            value /= 1024

        return f"{value:.2f} PB"

    @staticmethod
    def format_speed(value):
        """Format a transfer rate in bytes per second."""

        return (
            f"{SystemMonitorView.format_bytes(value)}/s"
        )

    # -----------------------------------------------------
    # METRIC UPDATES
    # -----------------------------------------------------

    def refresh_metrics(self, metrics):
        """Update the interface using a new metrics reading."""

        cpu = metrics["cpu"]
        memory = metrics["memory"]
        disk = metrics["disk"]
        network = metrics["network"]

        # CPU.
        cpu_usage = cpu["usage"]

        self.cpu_card.update_metric(
            cpu_usage,
            "Current processor utilisation",
        )

        # RAM.
        memory_usage = memory["usage"]

        memory_detail = (
            f"{self.format_bytes(memory['used'])} used "
            f"of {self.format_bytes(memory['total'])}"
        )

        self.memory_card.update_metric(
            memory_usage,
            memory_detail,
        )

        # Disk capacity.
        disk_usage = disk["usage"]

        disk_detail = (
            f"{self.format_bytes(disk['used'])} used "
            f"of {self.format_bytes(disk['total'])}"
        )

        self.disk_card.update_metric(
            disk_usage,
            disk_detail,
        )

        # Network activity.
        download = network["download_speed"]
        upload = network["upload_speed"]

        network_total = download + upload

        network_detail = (
            f"↓ {self.format_speed(download)}"
            f"    ↑ {self.format_speed(upload)}"
        )

        # This is only a visual activity indicator.
        # It does not represent network capacity utilisation.
        network_level = min(
            100,
            int(network_total / (1024 * 1024) * 10),
        )

        self.network_card.update_metric(
            network_level,
            network_detail,
            suffix="",
        )

        # Disk transfer rates.
        self.disk_activity_label.setText(
            f"Disk Read: "
            f"{self.format_speed(disk['read_speed'])}"
            f"    |    Disk Write: "
            f"{self.format_speed(disk['write_speed'])}"
        )

        # Network transfer rates.
        self.network_activity_label.setText(
            f"Download: {self.format_speed(download)}"
            f"    |    Upload: {self.format_speed(upload)}"
        )

        # Update graph histories.
        self.cpu_graph.add_value(cpu_usage)
        self.memory_graph.add_value(memory_usage)

        self.status_label.setText("MONITORING")
        self.status_label.setStyleSheet(
            f"color: {GREEN}; "
            "font-size: 11px; font-weight: 600;"
        )

    # -----------------------------------------------------
    # ERROR HANDLING
    # -----------------------------------------------------

    def handle_monitor_error(self, message):
        """Display a monitoring error in the status area."""

        self.status_label.setText("MONITORING ERROR")
        self.status_label.setStyleSheet(
            f"color: {ORANGE}; "
            "font-size: 11px; font-weight: 600;"
        )

        print(f"Windows Glance System Monitor: {message}")

    # -----------------------------------------------------
    # THREAD SHUTDOWN
    # -----------------------------------------------------

    def monitoring_stopped(self):
        """Update the status after the worker has stopped."""

        if self._closing:
            return

        self.status_label.setText("STOPPED")
        self.status_label.setStyleSheet(
            f"color: {SECONDARY_TEXT}; "
            "font-size: 11px;"
        )

    def closeEvent(self, event):
        """Request worker shutdown before closing the window."""

        if self.thread is None or not self.thread.isRunning():
            event.accept()
            return

        self._closing = True

        if self.worker is not None:
            self.worker.stop_requested.emit()

        # The worker's finished signal tells the thread to quit.
        # Wait for the thread to finish before destroying the GUI.
        if self.thread.wait(3000):
            event.accept()
        else:
            self._closing = False
            event.ignore()
            print(
                "Windows Glance: monitoring thread "
                "has not stopped yet. Close the window again."
            )