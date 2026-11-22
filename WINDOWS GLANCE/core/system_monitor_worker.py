
from PySide6.QtCore import QObject, QTimer, Signal, Slot

from core.system_monitor import SystemMonitor


class SystemMonitorWorker(QObject):
    metrics_ready = Signal(dict)
    error_occurred = Signal(str)
    finished = Signal()

    def __init__(self, interval=1000):
        super().__init__()

        self.interval = interval
        self.monitor = None
        self.timer = None

    @Slot()
    def start(self):
        # Initialise the collector inside the worker thread.
        self.monitor = SystemMonitor()

        # Create the timer in the worker thread.
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.collect_metrics)
        self.timer.start(self.interval)

        # Send the first reading immediately.
        self.collect_metrics()

    @Slot()
    def collect_metrics(self):
        try:
            metrics = self.monitor.get_metrics()
            self.metrics_ready.emit(metrics)

        except Exception as error:
            self.error_occurred.emit(str(error))

    @Slot()
    def stop(self):
        if self.timer is not None:
            self.timer.stop()
            self.timer.deleteLater()
            self.timer = None

        self.finished.emit()
