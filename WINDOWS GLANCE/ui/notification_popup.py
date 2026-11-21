from datetime import datetime

from PySide6.QtCore import (
    QEasingCurve, QParallelAnimationGroup, QPropertyAnimation, QRect, Qt
)
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QVBoxLayout, QWidget
)


class NotificationPopup(QWidget):
    WIDTH = 390
    HEIGHT = 440

    def __init__(self, store, owner):
        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        super().__init__(owner, flags)

        self.store = store
        self.owner = owner
        self._animation = None

        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, False)

        self._build_ui()
        self.store.changed.connect(self.refresh)
        self.refresh()

    def _build_ui(self):
        self.dark = self.owner.dark

        if self.dark:
            bg = "#20232B"
            surface = "#292D36"
            card = "#292D36"
            text = "#F4F5F8"
            muted = "#A5ADBA"
            border = "#3A404C"
            hover = "#343A46"
        else:
            bg = "#F9FAFC"
            surface = "#FFFFFF"
            card = "#F4F6F9"
            text = "#20242C"
            muted = "#737C8B"
            border = "#E3E7ED"
            hover = "#E9EDF3"

        accent = self.owner.accent.name()

        self.setStyleSheet(f"""
            QWidget {{
                font-family: "Segoe UI Variable", "Segoe UI";
                color: {text};
            }}

            QFrame#surface {{
                background: {bg};
                border: 1px solid {border};
                border-radius: 18px;
            }}

            QLabel#heading {{
                color: {text};
                font-size: 20px;
                font-weight: 650;
            }}

            QLabel#subheading {{
                color: {muted};
                font-size: 11px;
            }}

            QFrame#notification {{
                background: {card};
                border: 1px solid {border};
                border-radius: 12px;
            }}

            QLabel#itemTitle {{
                color: {text};
                font-size: 12px;
                font-weight: 650;
            }}

            QLabel#itemMessage {{
                color: {muted};
                font-size: 11px;
            }}

            QLabel#itemTime {{
                color: {muted};
                font-size: 10px;
            }}

            QPushButton {{
                color: {text};
                background: {surface};
                border: 1px solid {border};
                border-radius: 8px;
                padding: 7px 11px;
                font-size: 11px;
                font-weight: 600;
            }}

            QPushButton:hover {{
                background: {hover};
            }}

            QPushButton:pressed {{
                background: {border};
            }}

            QPushButton#accent {{
                background: {accent};
                color: white;
                border: 1px solid {accent};
            }}

            QPushButton#accent:hover {{
                background: {accent};
                border-color: {accent};
            }}

            QPushButton#close {{
                background: transparent;
                border: none;
                color: {muted};
                font-family: "Segoe MDL2 Assets";
                font-size: 13px;
                padding: 0;
            }}

            QPushButton#close:hover {{
                background: {hover};
                color: {text};
            }}

            QPushButton#dismiss {{
                background: transparent;
                border: none;
                color: {muted};
                font-size: 15px;
                padding: 0;
            }}

            QPushButton#dismiss:hover {{
                background: {hover};
                color: {text};
            }}

            QScrollArea {{
                border: none;
                background: transparent;
            }}

            QScrollBar:vertical {{
                background: transparent;
                width: 7px;
                margin: 2px 0 2px 0;
            }}

            QScrollBar::handle:vertical {{
                background: {border};
                border-radius: 3px;
                min-height: 28px;
            }}

            QScrollBar::handle:vertical:hover {{
                background: {muted};
            }}

            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{
                height: 0;
                background: transparent;
            }}

            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {{
                background: transparent;
            }}
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(7, 7, 7, 7)

        self.surface = QFrame()
        self.surface.setObjectName("surface")
        root.addWidget(self.surface)

        layout = QVBoxLayout(self.surface)
        layout.setContentsMargins(19, 17, 19, 15)
        layout.setSpacing(13)

        header = QHBoxLayout()
        header.setSpacing(8)

        title_col = QVBoxLayout()
        title_col.setSpacing(3)

        heading = QLabel("Notifications")
        heading.setObjectName("heading")

        self.subtitle = QLabel()
        self.subtitle.setObjectName("subheading")

        title_col.addWidget(heading)
        title_col.addWidget(self.subtitle)

        header.addLayout(title_col)
        header.addStretch()

        close_btn = QPushButton("\uE8BB")
        close_btn.setObjectName("close")
        close_btn.setFixedSize(32, 32)
        close_btn.setToolTip("Close")
        close_btn.clicked.connect(self.close_animated)
        header.addWidget(close_btn)

        layout.addLayout(header)

        actions = QHBoxLayout()
        actions.setSpacing(8)

        read_btn = QPushButton("Mark all as read")
        read_btn.clicked.connect(self.store.mark_all_read)

        clear_btn = QPushButton("Clear all")
        clear_btn.clicked.connect(self.store.clear)

        actions.addWidget(read_btn)
        actions.addWidget(clear_btn)
        actions.addStretch()

        layout.addLayout(actions)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        self.list_widget = QWidget()
        self.list_layout = QVBoxLayout(self.list_widget)
        self.list_layout.setContentsMargins(1, 3, 5, 3)
        self.list_layout.setSpacing(9)

        self.scroll.setWidget(self.list_widget)
        layout.addWidget(self.scroll, 1)

        self.footer = QLabel("WINDOWS GLANCE")
        self.footer.setObjectName("subheading")
        self.footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.footer)

    def _clear_rows(self):
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def _time_label(self, value):
        try:
            dt = datetime.fromisoformat(value)
            return dt.strftime("%H:%M")
        except (ValueError, TypeError):
            return ""

    def refresh(self):
        self._clear_rows()

        items = self.store.get_all()
        unread = self.store.unread_count()

        self.subtitle.setText(
            f"{unread} unread  \u00B7  {len(items)} total"
        )

        if not items:
            empty = QLabel(
                "You're all caught up.\n"
                "New notifications will appear here."
            )
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setWordWrap(True)
            empty.setStyleSheet(
                "color: palette(mid); font-size: 12px; padding: 65px 12px;"
            )
            self.list_layout.addWidget(empty)
            self.list_layout.addStretch()
            return

        for item in items:
            card = QFrame()
            card.setObjectName("notification")

            row = QHBoxLayout(card)
            row.setContentsMargins(12, 11, 9, 11)
            row.setSpacing(10)

            dot = QLabel(
                "\u25CF" if item.get("unread") else "\u25CB"
            )
            dot_color = (
                self.owner.accent.name()
                if item.get("unread")
                else "#858B98"
            )
            dot.setStyleSheet(
                f"color: {dot_color}; font-size: 10px; border: none;"
            )
            dot.setFixedWidth(9)
            row.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)

            content = QVBoxLayout()
            content.setSpacing(4)

            title = QLabel(str(item.get("title", "Notification")))
            title.setObjectName("itemTitle")
            title.setWordWrap(True)

            message = QLabel(str(item.get("message", "")).strip())
            message.setObjectName("itemMessage")
            message.setWordWrap(True)

            source_time = (
                str(item.get("source", "Windows Glance"))
                + "  \u00B7  "
                + self._time_label(item.get("timestamp"))
            )

            metadata = QLabel(source_time)
            metadata.setObjectName("itemTime")
            metadata.setWordWrap(True)

            content.addWidget(title)
            if message.text():
                content.addWidget(message)
            content.addWidget(metadata)

            row.addLayout(content, 1)

            notification_id = item["id"]

            dismiss = QPushButton("\uE711")
            dismiss.setObjectName("dismiss")
            dismiss.setFixedSize(26, 28)
            dismiss.setToolTip("Dismiss notification")
            dismiss.clicked.connect(
                lambda checked=False, nid=notification_id:
                self.store.dismiss(nid)
            )
            row.addWidget(dismiss, 0, Qt.AlignmentFlag.AlignTop)

            if item.get("unread"):
                mark_read = QPushButton("Read")
                mark_read.setObjectName("accent")
                mark_read.setToolTip("Mark as read")
                mark_read.clicked.connect(
                    lambda checked=False, nid=notification_id:
                    self.store.mark_read(nid)
                )
                row.addWidget(mark_read, 0, Qt.AlignmentFlag.AlignBottom)

            self.list_layout.addWidget(card)

        self.list_layout.addStretch()

    def show_below(self, anchor_rect):
        if self._animation:
            self._animation.stop()

        screen = QApplication.screenAt(anchor_rect.center())
        if screen is None:
            screen = QApplication.primaryScreen()

        area = screen.availableGeometry()

        x = anchor_rect.center().x() - self.WIDTH // 2
        x = max(area.left() + 8, min(x, area.right() - self.WIDTH + 1))

        y = anchor_rect.bottom() + 16
        if y + self.HEIGHT > area.bottom():
            y = max(area.top() + 8, anchor_rect.top() - self.HEIGHT - 16)

        target = QRect(x, y, self.WIDTH, self.HEIGHT)
        start = target.translated(0, -13)

        self.refresh()
        self.setGeometry(start)
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()

        group = QParallelAnimationGroup(self)

        geometry = QPropertyAnimation(self, b"geometry")
        geometry.setDuration(260)
        geometry.setStartValue(start)
        geometry.setEndValue(target)
        geometry.setEasingCurve(QEasingCurve.Type.OutCubic)

        opacity = QPropertyAnimation(self, b"windowOpacity")
        opacity.setDuration(230)
        opacity.setStartValue(0.0)
        opacity.setEndValue(1.0)
        opacity.setEasingCurve(QEasingCurve.Type.OutCubic)

        group.addAnimation(geometry)
        group.addAnimation(opacity)

        self._animation = group
        group.start()

    def close_animated(self):
        if not self.isVisible():
            return

        if self._animation:
            self._animation.stop()

        group = QParallelAnimationGroup(self)

        geometry = QPropertyAnimation(self, b"geometry")
        geometry.setDuration(170)
        geometry.setStartValue(self.geometry())
        geometry.setEndValue(self.geometry().translated(0, -9))
        geometry.setEasingCurve(QEasingCurve.Type.InCubic)

        opacity = QPropertyAnimation(self, b"windowOpacity")
        opacity.setDuration(170)
        opacity.setStartValue(self.windowOpacity())
        opacity.setEndValue(0.0)

        group.addAnimation(geometry)
        group.addAnimation(opacity)
        group.finished.connect(self.hide)

        self._animation = group
        group.start()
