import time
from collections import deque
from PySide6.QtCore import Qt, QThread, Signal
from core.system_monitor_worker import SystemMonitorWorker
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QFrame,
    QLabel,
    QProgressBar,
    QSizePolicy,
)

# Theme
BACKGROUND = "#0B1220"
CARD_BACKGROUND = "#121A27"
CARD_BORDER = "#26364D"
PRIMARY_TEXT = "#E5EDF7"
SECONDARY_TEXT = "#8293AC"
ACCENT = "#22D3EE"
GREEN = "#34D399"
BLUE = "#60A5FA"
ORANGE = "#FBBF24"


# Reusable metric card
class MetricCard(QFrame):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setObjectName("metricCard")
        self.setStyleSheet("""
            QFrame#metricCard {
                background: #121A27;
                border: 1px solid #26364D;
                border-radius: 10px;
            }
            QLabel {
                border: none;
                background: transparent;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        self.title_label = QLabel(title.upper())
        self.title_label.setStyleSheet(
            f"color: {SECONDARY_TEXT}; font-size: 10px; font-weight: 700;"
        )

        self.value_label = QLabel("0.0%")
        self.value_label.setStyleSheet(
            f"color: {PRIMARY_TEXT}; font-size: 20px; font-weight: 700;"
        )

        self.detail_label = QLabel("Waiting for Data")
        self.detail_label.setStyleSheet(
            f"color: {SECONDARY_TEXT}; font-size: 11px;"
        )

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(5)
        self.progress.setStyleSheet("""
            QProgressBar {
                background: #26364D;
                border: none;
                border-radius: 2px;
            }
            QProgressBar::chunk {
                background: #22D3EE;
                border-radius: 2px;
            }
        """)

        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)
        layout.addWidget(self.progress)
        layout.addWidget(self.detail_label)

    def update_card(self, value, percentage, detail):
        self.value_label.setText(value)
        self.progress.setValue(max(0, min(100, int(percentage))))
        self.detail_label.setText(detail)


# Live graph
class LiveGraph(QWidget):
    def __init__(self, title, color, parent=None):
        super().__init__(parent)
        self.title = title
        self.color = QColor(color)
        self.values = deque([0.0] * 60, maxlen=60)
        self.setMinimumHeight(150)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

    def add_value(self, value):
        self.values.append(max(0.0, float(value)))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect().adjusted(12, 12, -12, -12)

        # Graph background
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(CARD_BACKGROUND))
        painter.drawRoundedRect(rect, 8, 8)

        # Graph title
        painter.setPen(QColor(SECONDARY_TEXT))
        painter.drawText(
            rect.adjusted(10, 8, -10, -8),
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
            self.title,
        )

        graph_rect = rect.adjusted(10, 35, -10, -15)

        # Horizontal guide lines
        painter.setPen(QPen(QColor(CARD_BORDER), 1))
        for i in range(1, 4):
            y = graph_rect.top() + (graph_rect.height() * i / 4)
            painter.drawLine(
                int(graph_rect.left()),
                int(y),
                int(graph_rect.right()),
                int(y),
            )

        # Graph line
        if len(self.values) > 1:
            maximum = max(100.0, max(self.values))
            points = []
            for i, value in enumerate(self.values):
                x = graph_rect.left() + (
                    i / (len(self.values) - 1)
                ) * graph_rect.width()
                y = graph_rect.bottom() - (value / maximum) * graph_rect.height()
                points.append((int(x), int(y)))

            painter.setPen(QPen(self.color, 2))
            for i in range(1, len(points)):
                painter.drawLine(
                    points[i - 1][0],
                    points[i - 1][1],
                    points[i][0],
                    points[i][1],
                )

        painter.end()


# System Monitor View
class SystemMonitorView(QWidget):
    stop_monitoring_requested = Signal()
    def __init__(self, parent=None):
        super().__init__(parent)
    def start_monitoring(self):
        self.monitor_thread = QThread(self)

        self.monitor_worker = SystemMonitorWorker()
        self.monitor_worker.moveToThread(self.monitor_thread)

        # Start the worker when the thread starts.
        self.monitor_thread.started.connect(
            self.monitor_worker.start
        )

        # Receive metrics on the interface thread.
        self.monitor_worker.metrics_ready.connect(
            self.refresh_metrics
        )

        self.monitor_worker.error_occurred.connect(
            self.handle_monitor_error
        )

        # Request a safe shutdown.
        self.stop_monitoring_requested.connect(
            self.monitor_worker.stop,
            Qt.ConnectionType.QueuedConnection
        )

        self.monitor_worker.finished.connect(
            self.monitor_thread.quit
        )

        self.monitor_worker.finished.connect(
            self.monitor_worker.deleteLater
        )

        self.monitor_thread.finished.connect(
            self.monitor_thread.deleteLater
        )

        self.monitor_thread.start()
        self.cpu_history = deque(maxlen=60)
        self.ram_history = deque(maxlen=60)
        self.setStyleSheet(
            f"background: {BACKGROUND}; color: {PRIMARY_TEXT}; font-family: 'Segoe UI';"
        )
        self.build_interface()
        self.start_monitoring()

    def build_interface(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 24)
        root.setSpacing(16)

        # Header
        header = QHBoxLayout()
        heading = QVBoxLayout()
        heading.setSpacing(4)

        title = QLabel("System Monitor")
        title.setStyleSheet(
            f"color: {PRIMARY_TEXT}; font-size: 23px; font-weight: 700; border: none;"
        )

        subtitle = QLabel("Real-time performance and resource utilisation")
        subtitle.setStyleSheet(
            f"color: {SECONDARY_TEXT}; font-size: 11px; border: none;"
        )

        heading.addWidget(title)
        heading.addWidget(subtitle)

        self.status_label = QLabel("INITIALISING")
        self.status_label.setStyleSheet(
            f"color: {GREEN}; font-size: 10px; font-weight: 700; border: none;"
        )

        header.addLayout(heading)
        header.addStretch()
        header.addWidget(self.status_label)
        root.addLayout(header)

        # Metric cards
        cards = QGridLayout()
        cards.setSpacing(12)

        self.cpu_card = MetricCard("Processor")
        self.ram_card = MetricCard("Memory")
        self.disk_card = MetricCard("Storage")
        self.network_card = MetricCard("Network")

        cards.addWidget(self.cpu_card, 0, 0)
        cards.addWidget(self.ram_card, 0, 1)
        cards.addWidget(self.disk_card, 1, 0)
        cards.addWidget(self.network_card, 1, 1)
        root.addLayout(cards)

        # Graphs
        graph_layout = QHBoxLayout()
        graph_layout.setSpacing(12)

        self.cpu_graph = LiveGraph("CPU UTILISATION", ACCENT)
        self.ram_graph = LiveGraph("MEMORY UTILISATION", BLUE)

        graph_layout.addWidget(self.cpu_graph)
        graph_layout.addWidget(self.ram_graph)
        root.addLayout(graph_layout, 1)

        # Footer
        self.footer = QLabel("Refresh interval: 1 second")
        self.footer.setStyleSheet(
            f"color: {SECONDARY_TEXT}; font-size: 10px; border: none;"
        )
        root.addWidget(self.footer)

    def refresh_metrics(self, metrics):
        cpu = metrics["cpu"]["usage"]
        ram = metrics["memory"]["usage"]

        ram_used = self.format_bytes(
            metrics["memory"]["used"]
        )
        ram_total = self.format_bytes(
            metrics["memory"]["total"]
        )

        disk_used = self.format_bytes(
            metrics["disk"]["used"]
        )
        disk_total = self.format_bytes(
            metrics["disk"]["total"]
        )

        download = self.format_bytes(
            metrics["network"]["download_speed"]
        )
        upload = self.format_bytes(
            metrics["network"]["upload_speed"]
        )

        disk_read = self.format_bytes(
            metrics["disk"]["read_speed"]
        )
        disk_write = self.format_bytes(
            metrics["disk"]["write_speed"]
        )

        self.cpu_card.update_card(
            f"{cpu:.1f}%",
            cpu,
            "Current processor utilisation"
        )

        self.ram_card.update_card(
            f"{ram:.1f}%",
            ram,
            f"{ram_used} used of {ram_total}"
        )

        self.disk_card.update_card(
            f"{metrics['disk']['usage']:.1f}%",
            metrics["disk"]["usage"],
            f"{disk_used} / {disk_total} | "
            f"Read: {disk_read}/s | Write: {disk_write}/s"
        )

        self.network_card.update_card(
            f"↓ {download}/s",
            0,
            f"Upload: {upload}/s"
        )

        self.cpu_graph.add_value(cpu)
        self.ram_graph.add_value(ram)

        self.status_label.setText("● LIVE")
        self.footer.setText("Refresh interval: 1 second")
    
    def handle_monitor_error(self, message):
        self.status_label.setText("● MONITOR ERROR")
        self.footer.setText(message)
        
    def closeEvent(self, event):
        if self.monitor_thread.isRunning():
            self.stop_monitoring_requested.emit()

            if not self.monitor_thread.wait(3000):
                event.ignore()
                return

        event.accept()

    @staticmethod
    def format_bytes(value):
        units = ["B", "KB", "MB", "GB", "TB"]
        for unit in units:
            if value < 1024:
                return f"{value:.1f} {unit}"
            value /= 1024
        return f"{value:.1f} PB"

    def refresh_metrics(self):
        try:
            metrics = self.monitor.get_metrics()

            cpu = metrics["cpu"]["usage"]
            ram = metrics["memory"]["usage"]
            ram_used = self.format_bytes(metrics["memory"]["used"])
            ram_total = self.format_bytes(metrics["memory"]["total"])

            disk_usage = metrics["disk"]["usage"]
            disk_used = self.format_bytes(metrics["disk"]["used"])
            disk_total = self.format_bytes(metrics["disk"]["total"])

            download = self.format_bytes(metrics["network"]["download_speed"])
            upload = self.format_bytes(metrics["network"]["upload_speed"])

            # Processor
            self.cpu_card.update_card(
                f"{cpu:.1f}%", cpu, "Active CPU load"
            )

            # Memory
            self.ram_card.update_card(
                f"{ram:.1f}%", ram, f"{ram_used} used of {ram_total}"
            )

            # Storage
            self.disk_card.update_card(
                f"{disk_usage:.1f}%",
                disk_usage,
                f"{disk_used} used of {disk_total}",
            )

            # Network
            self.network_card.update_card(
                f"{download}/s", 0, f"Upload: {upload}/s"
            )

            # Historical graphs
            self.cpu_graph.add_value(cpu)
            self.ram_graph.add_value(ram)

            self.status_label.setText("LIVE")
        except Exception as error:
            self.status_label.setText("MONITOR ERROR")
            self.footer.setText(str(error))
