import sys

from PySide6.QtWidgets import QApplication

from ui.system_monitor_view import SystemMonitorView


def main():
    app = QApplication(sys.argv)

    window = SystemMonitorView()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()