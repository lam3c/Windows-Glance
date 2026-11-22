
import sys

from PySide6.QtWidgets import QApplication
from ui.system_monitor_view import SystemMonitorView

app = QApplication(sys.argv)

window = SystemMonitorView()
window.setWindowTitle("Windows Glance - System Monitor")
window.resize(850, 650)
window.show()

sys.exit(app.exec())
