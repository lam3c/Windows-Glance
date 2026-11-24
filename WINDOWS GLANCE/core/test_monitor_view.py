
import sys
from PySide6.QtWidgets import QApplication
from ui.device_stats import SystemMonitorView


def main():
    app = QApplication(sys.argv)
    window = SystemMonitorView(start_monitoring=True)
    window.setWindowTitle("Windows Glance - System Monitor")
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
