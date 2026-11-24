"""Compatibility shim: the dashboard widget lives in ui/device_stats.py."""

from ui.device_stats import SystemMonitorView, main

__all__ = ["SystemMonitorView", "main"]