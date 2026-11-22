import os
import time
import psutil


class SystemMonitor:
    def __init__(self):
        # Prepare CPU measurement.
        psutil.cpu_percent(interval=None)

        # Save initial counters for calculating activity rate.
        self.previous_time = time.monotonic()
        self.previous_disk = psutil.disk_io_counters()
        self.previous_network = psutil.net_io_counters()

    @staticmethod
    def calculate_rate(current, previous, elapsed):
        if current is None or previous is None or elapsed <= 0:
            return 0.0
        difference = max(0, current - previous)
        return difference / elapsed

    def get_metrics(self):
        # Measure elapsed time between readings.
        current_time = time.monotonic()
        elapsed = current_time - self.previous_time

        # CPU usage.
        cpu = psutil.cpu_percent(interval=None)

        # RAM usage.
        memory = psutil.virtual_memory()

        # Disk activity.
        current_disk = psutil.disk_io_counters()
        if current_disk and self.previous_disk:
            disk_read = self.calculate_rate(
                current_disk.read_bytes, self.previous_disk.read_bytes, elapsed
            )
            disk_write = self.calculate_rate(
                current_disk.write_bytes, self.previous_disk.write_bytes, elapsed
            )
        else:
            disk_read = 0.0
            disk_write = 0.0

        # Network activity.
        current_network = psutil.net_io_counters()
        if current_network and self.previous_network:
            download = self.calculate_rate(
                current_network.bytes_recv, self.previous_network.bytes_recv, elapsed
            )
            upload = self.calculate_rate(
                current_network.bytes_sent, self.previous_network.bytes_sent, elapsed
            )
        else:
            download = 0.0
            upload = 0.0

        # Main Windows system drive usage.
        system_drive = os.environ.get("SystemDrive", "C:")
        disk = psutil.disk_usage(system_drive + "\\")

        # Update counters for the next measurement.
        self.previous_time = current_time
        self.previous_disk = current_disk
        self.previous_network = current_network

        return {
            "cpu": {
                "usage": cpu
            },
            "memory": {
                "used": memory.used,
                "total": memory.total,
                "usage": memory.percent
            },
            "disk": {
                "used": disk.used,
                "total": disk.total,
                "usage": disk.percent,
                "read_speed": disk_read,
                "write_speed": disk_write,
            },
            "network": {
                "download_speed": download,
                "upload_speed": upload
            }
        }
