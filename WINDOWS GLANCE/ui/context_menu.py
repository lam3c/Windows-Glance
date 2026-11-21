
"""Context menu and settings interactions for Windows Glance."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QApplication, QInputDialog, QMenu


class ContextMenuController:

    def __init__(self, window, startup_enabled, set_startup):
        self.window = window
        self.startup_enabled = startup_enabled
        self.set_startup = set_startup

    def _style_menu(self, menu):
        w = self.window
        th = w.th

        bg = f"rgb({th.bg.red()},{th.bg.green()},{th.bg.blue()})"
        txt = f"rgb({th.text.red()},{th.text.green()},{th.text.blue()})"
        hov = "rgba(255,255,255,0.09)" if th.dark else "rgba(0,0,0,0.06)"
        brd = "rgba(255,255,255,0.10)" if th.dark else "rgba(0,0,0,0.12)"

        menu.setWindowFlags(
            menu.windowFlags()
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )

        menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        menu.setStyleSheet(f"""
            QMenu {{
                background:{bg};
                color:{txt};
                border:1px solid {brd};
                border-radius:8px;
                padding:4px;
                font-family:'Segoe UI Variable Text','Segoe UI';
                font-size:13px;
            }}
            QMenu::item {{
                padding:7px 28px 7px 12px;
                border-radius:4px;
                margin:2px 2px;
            }}
            QMenu::item:selected {{
                background:{hov};
            }}
            QMenu::separator {{
                height:1px;
                background:{brd};
                margin:4px 8px;
            }}
        """)

    def show(self, global_position):
        w = self.window

        menu = QMenu(w)
        self._style_menu(menu)

        position_menu = menu.addMenu("Position")
        self._style_menu(position_menu)

        position_group = QActionGroup(position_menu)

        for key, label in w.ANCHORS:
            action = QAction(label, position_menu, checkable=True)
            action.setChecked(key == w.anchor)
            action.setData(key)
            position_group.addAction(action)
            position_menu.addAction(action)

        unit_menu = menu.addMenu("Weather units")
        self._style_menu(unit_menu)

        unit_group = QActionGroup(unit_menu)

        for unit, label in (
            ("C", "Celsius (°C)"),
            ("F", "Fahrenheit (°F)"),
        ):
            action = QAction(label, unit_menu, checkable=True)
            action.setChecked(w.unit == unit)
            action.setData("unit:" + unit)
            unit_group.addAction(action)
            unit_menu.addAction(action)

        city_action = menu.addAction("Weather location…")

        menu.addSeparator()

        startup_action = QAction("Run at startup", menu, checkable=True)
        startup_action.setChecked(self.startup_enabled())
        menu.addAction(startup_action)

        menu.addSeparator()

        exit_action = menu.addAction("Exit")

        w._menu_open = True

        try:
            chosen = menu.exec(global_position)
        finally:
            w._menu_open = False

        if chosen is None:
            return

        if chosen is exit_action:
            QApplication.quit()

        elif chosen is startup_action:
            self.set_startup(startup_action.isChecked())

        elif chosen is city_action:
            self.ask_city()

        else:
            data = chosen.data()

            if isinstance(data, str) and data in dict(w.ANCHORS):
                w.anchor = data
                w.settings.setValue("anchor", data)
                w.apply_geometry()

            elif isinstance(data, str) and data.startswith("unit:"):
                w.unit = data[5:]
                w.settings.setValue("wx_unit", w.unit)
                w.wx_worker.configure(unit=w.unit)
                w.wx_worker.refresh()

    def ask_city(self):
        w = self.window

        w._menu_open = True

        try:
            city, accepted = QInputDialog.getText(
                None,
                "Weather location",
                "City (leave empty to detect automatically):",
                text=w.city,
            )
        finally:
            w._menu_open = False

        if accepted:
            w.city = city.strip()
            w.settings.setValue("wx_city", w.city)
            w.wx_msg = "Loading weather…"
            w.wx_worker.configure(city=w.city)
            w.wx_worker.refresh()
            w.update()
