import time
from core.system_monitor import SystemMonitor

monitor = SystemMonitor()

while True:
    metrics = monitor.get_metrics()
    print("\n--- WINDOWS GLANCE SYSTEM MONITOR ---")
    print(f"CPU: {metrics['cpu']['usage']}%")
    print(f"RAM: {metrics['memory']['usage']}%")
    print(f"Disk: {metrics['disk']['usage']}%")
    print(f"Disk Read: {metrics['disk']['read_speed'] / 1024:.2f} KB/s")
    print(f"Disk Write: {metrics['disk']['write_speed'] / 1024:.2f} KB/s")
    print(f"Download: {metrics['network']['download_speed'] / 1024:.2f} KB/s")
    print(f"Upload: {metrics['network']['upload_speed'] / 1024:.2f} KB/s")
    time.sleep(1)
