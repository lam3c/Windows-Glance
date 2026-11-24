"""Standalone test launcher for the system statistics dashboard."""

import sys

from PySide6.QtWidgets import QApplication

from ui.device_stats import SystemMonitorView


def main():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)

    window = SystemMonitorView(start_monitoring=True)
    window.setWindowTitle("Windows Glance | System Statistics Test")
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
