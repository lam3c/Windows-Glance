"""Persistent notification storage for Windows Glance."""

import json
import os
import uuid
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Signal


class NotificationStore(QObject):
    changed = Signal()

    MAX_ITEMS = 100

    def __init__(self):
        super().__init__()

        app_data = Path(os.environ.get("APPDATA", Path.home()))
        self.directory = app_data / "WindowsGlance" / "Glance"
        self.file_path = self.directory / "notifications.json"

        self.items = []
        self.last_error = None

        self._load()

    def _load(self):
        try:
            if self.file_path.exists():
                data = json.loads(self.file_path.read_text(encoding="utf-8"))

                if isinstance(data, list):
                    self.items = [
                        item for item in data
                        if isinstance(item, dict)
                        and item.get("id")
                        and item.get("title")
                    ][:self.MAX_ITEMS]

        except (OSError, json.JSONDecodeError):
            self.items = []

    def _save(self):
        try:
            self.directory.mkdir(parents=True, exist_ok=True)

            temporary = self.file_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(self.items, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )

            temporary.replace(self.file_path)
            self.last_error = None
            return True

        except OSError as exc:
            self.last_error = str(exc)
            return False

    def add(self, title, message="", source="Windows Glance",
            category="info"):

        notification = {
            "id": uuid.uuid4().hex,
            "title": str(title)[:200],
            "message": str(message)[:2000],
            "source": str(source)[:100],
            "category": str(category)[:30],
            "timestamp": datetime.now().astimezone().isoformat(
                timespec="seconds"
            ),
            "unread": True,
        }

        self.items.insert(0, notification)
        self.items = self.items[:self.MAX_ITEMS]

        self._save()
        self.changed.emit()

        return dict(notification)

    def get_all(self):
        return [dict(item) for item in self.items]

    def get_unread(self):
        return [
            dict(item) for item in self.items
            if item.get("unread", False)
        ]

    def unread_count(self):
        return sum(
            1 for item in self.items
            if item.get("unread", False)
        )

    def mark_read(self, notification_id):
        for item in self.items:
            if item["id"] == notification_id:
                if item.get("unread"):
                    item["unread"] = False
                    self._save()
                    self.changed.emit()
                return True

        return False

    def mark_all_read(self):
        changed = False

        for item in self.items:
            if item.get("unread"):
                item["unread"] = False
                changed = True

        if changed:
            self._save()
            self.changed.emit()

    def dismiss(self, notification_id):
        original_count = len(self.items)

        self.items = [
            item for item in self.items
            if item["id"] != notification_id
        ]

        if len(self.items) != original_count:
            self._save()
            self.changed.emit()
            return True

        return False

    def clear(self):
        if self.items:
            self.items.clear()
            self._save()
            self.changed.emit()
