from PySide6.QtCore import QObject, QTimer, Signal, Slot

from core.system_monitor import SystemMonitor


class SystemMonitorWorker(QObject):
    """Collects system metrics in a dedicated worker thread."""

    metrics_ready = Signal(dict)
    error_occurred = Signal(str)
    finished = Signal()
    stop_requested = Signal()

    def __init__(self, interval=1000):
        super().__init__()

        self.interval = max(100, int(interval))
        self.monitor = None
        self.timer = None
        self.running = False
        self._finished = False

        # Requests from the GUI are handled in the worker's thread.
        self.stop_requested.connect(self.stop)

    @Slot()
    def start(self):
        """Initialise the collector and begin periodic measurements."""

        if self._finished:
            return

        try:
            self.monitor = SystemMonitor()

            self.timer = QTimer(self)
            self.timer.setInterval(self.interval)
            self.timer.timeout.connect(self.collect_metrics)

            self.running = True
            self.timer.start()

            # Deliver the first reading without waiting for the timer.
            self.collect_metrics()

        except Exception as error:
            self.error_occurred.emit(
                f"Could not start system monitoring: {error}"
            )
            self.stop()

    @Slot()
    def collect_metrics(self):
        """Collect one reading and send it to the interface."""

        if not self.running or self.monitor is None:
            return

        try:
            metrics = self.monitor.get_metrics()
            self.metrics_ready.emit(metrics)

        except Exception as error:
            self.error_occurred.emit(
                f"Metric collection failed: {error}"
            )

    @Slot()
    def stop(self):
        """Stop periodic collection and signal completion."""

        if self._finished:
            return

        self.running = False
        self._finished = True

        if self.timer is not None:
            self.timer.stop()
            self.timer.deleteLater()
            self.timer = None

        self.finished.emit()