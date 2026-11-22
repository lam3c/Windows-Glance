import os
import time

import psutil


class SystemMonitor:
    """Collect CPU, memory, disk and network performance metrics."""

    def __init__(self):
        # Prime CPU measurement so subsequent readings are meaningful.
        psutil.cpu_percent(interval=None)

        self.previous_time = time.monotonic()
        self.previous_disk = psutil.disk_io_counters()
        self.previous_network = psutil.net_io_counters()

        system_drive = os.environ.get("SystemDrive", "C:")
        self.system_drive = system_drive + "\\"

    @staticmethod
    def calculate_rate(current, previous, elapsed):
        """Calculate a counter's change per second."""

        if current is None or previous is None or elapsed <= 0:
            return 0.0

        difference = max(0, current - previous)
        return difference / elapsed

    def get_metrics(self):
        """Collect and return the latest system metrics."""

        current_time = time.monotonic()
        elapsed = current_time - self.previous_time

        # CPU utilisation.
        cpu_usage = psutil.cpu_percent(interval=None)

        # Memory utilisation.
        memory = psutil.virtual_memory()

        # Disk I/O rates.
        current_disk = psutil.disk_io_counters()

        if current_disk is not None and self.previous_disk is not None:
            disk_read = self.calculate_rate(
                current_disk.read_bytes,
                self.previous_disk.read_bytes,
                elapsed,
            )

            disk_write = self.calculate_rate(
                current_disk.write_bytes,
                self.previous_disk.write_bytes,
                elapsed,
            )
        else:
            disk_read = 0.0
            disk_write = 0.0

        # Network transfer rates.
        current_network = psutil.net_io_counters()

        if current_network is not None and self.previous_network is not None:
            download_speed = self.calculate_rate(
                current_network.bytes_recv,
                self.previous_network.bytes_recv,
                elapsed,
            )

            upload_speed = self.calculate_rate(
                current_network.bytes_sent,
                self.previous_network.bytes_sent,
                elapsed,
            )
        else:
            download_speed = 0.0
            upload_speed = 0.0

        # System drive capacity and utilisation.
        disk = psutil.disk_usage(self.system_drive)

        # Save counters for the next measurement.
        self.previous_time = current_time
        self.previous_disk = current_disk
        self.previous_network = current_network

        return {
            "cpu": {
                "usage": cpu_usage,
            },
            "memory": {
                "used": memory.used,
                "total": memory.total,
                "usage": memory.percent,
            },
            "disk": {
                "used": disk.used,
                "total": disk.total,
                "usage": disk.percent,
                "read_speed": disk_read,
                "write_speed": disk_write,
            },
            "network": {
                "download_speed": download_speed,
                "upload_speed": upload_speed,
            },
        }