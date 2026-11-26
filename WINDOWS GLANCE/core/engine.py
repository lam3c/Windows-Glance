"""
Windows Glance - a Windows 11 style status pill with weather, quick toggles,
live system stats, a media widget and a built-in voice assistant.

Install (Python 3.9 - 3.12):
    pip install PySide6 psutil winsdk

Python 3.13+ (winsdk has no wheels there), use the modular winrt packages:
    pip install PySide6 psutil winrt-runtime winrt-Windows.Foundation ^
        winrt-Windows.Foundation.Collections winrt-Windows.Media.Control ^
        winrt-Windows.Storage.Streams winrt-Windows.Devices.Radios ^
        winrt-Windows.Networking.Connectivity

States
    idle      : tiny see-through sliver at the top of the screen (click-through).
    hover     : cursor over it -> compact pill: clock, weather, Wi-Fi, Bluetooth,
                battery. A mini media player appears when music is playing.
    expanded  : click the pill -> full panel: weather + clock, Wi-Fi / Bluetooth
                toggles, battery, CPU / RAM / DISK / NET stats, media player
                and volume buttons.
    stats     : click any stat cell -> the panel switches to a live performance
                view (CPU, memory, storage, network) with graphs. The back
                arrow returns to the panel.

Live activities (the pill pops out by itself)
    charging  : plug in or unplug the charger.
    assistant : say "Glance", then a command. Say "Glance, help" for the list.

Controls
    Wi-Fi / Bluetooth tiles : click = toggle.  Right-click = Windows Settings.
    Stats row               : click = open the performance view.
    Bell                    : opens the notification popup.
    Right-click (elsewhere) : menu (position, weather, run at startup, exit).
"""

import math
import os
import sys
import time

from collections import deque

from PySide6.QtCore import (
    QEasingCurve,
    QPointF,
    QRectF,
    QSettings,
    Qt,
    QThread,
    QTimer,
    QVariantAnimation,
)

from PySide6.QtWidgets import QApplication, QWidget

# Shared helpers, constants and theme. These names are re-exported from here
# so other modules can still do "from core.engine import ...".
from core.common import *  # noqa: F401,F403

from core.weather_worker import WeatherWorker
from core.system_worker import SystemWorker
from core.system_worker import WINRT as _WINRT_MODULES

try:
    from core.system_monitor_worker import SystemMonitorWorker
    MONITOR_AVAILABLE = True
except Exception:  # most likely psutil is not installed
    SystemMonitorWorker = None
    MONITOR_AVAILABLE = False

from core.notification_store import NotificationStore
from ui.context_menu import ContextMenuController
from ui.notification_popup import NotificationPopup
from ui.painter import PainterMixin
from ui.charging import ChargingMixin
from ui.assistant_view import AssistantMixin

from ui.stats_view import (
    STATS_BACK,
    STATS_HISTORY,
    STATS_TASKMGR,
    STATS_VIEW_H,
    StatsViewMixin,
)


WINRT = _WINRT_MODULES is not None


# --------------------------------------------------------------------------- #
# Main widget
# --------------------------------------------------------------------------- #

class WindowsGlance(
    AssistantMixin,
    ChargingMixin,
    StatsViewMixin,
    PainterMixin,
    QWidget,
):

    ANCHORS = (
        ("top-center", "Top center"),
        ("top-right", "Top right"),
        ("top-left", "Top left"),
    )

    def __init__(self):
        super().__init__()

        self.settings = QSettings("WindowsGlance", "Glance")

        self.anchor = str(self.settings.value("anchor", "top-center"))

        if self.anchor not in dict(self.ANCHORS):
            self.anchor = "top-center"

        self.unit = str(self.settings.value("wx_unit", "C"))

        if self.unit not in ("C", "F"):
            self.unit = "C"

        self.city = str(self.settings.value("wx_city", "") or "")

        # Notifications.
        self.notifications = NotificationStore()
        self.notifications.changed.connect(self.update)
        self.notification_view = False  # kept for compatibility

        # Interface state.
        self.state = "idle"
        self.stats_view = False
        self.hover_item = None

        self._left_at = None
        self._enter_at = None
        self._menu_open = False
        self._radio_lock_until = 0.0

        # System status.
        self.battery = None
        self.wifi_text = "Checking…"
        self.wifi_connected = False
        self.wifi_on = None
        self.bt = None

        # Weather.
        self.weather = None
        self.wx_msg = "Loading weather…"

        # Media.
        self.media = None
        self.media_t = 0.0
        self.art = None
        self._art_src = QRectF()
        self._ignore_play_until = 0.0

        # Performance stats.
        self.monitor_available = MONITOR_AVAILABLE
        self.monitor_thread = None
        self.monitor_worker = None
        self.metrics = None
        self.metrics_error = ""
        self.cpu_history = deque(maxlen=STATS_HISTORY)
        self.mem_history = deque(maxlen=STATS_HISTORY)
        self.disk_history = deque(maxlen=STATS_HISTORY)
        self.net_history = deque(maxlen=STATS_HISTORY)

        # Theme.
        self.dark = system_uses_dark()
        self.th = make_theme(self.dark)
        self.accent = system_accent(self.dark)

        self.notification_popup = NotificationPopup(self.notifications, self)
        self._open_notification_popup = False

        # Window configuration.
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMouseTracking(True)

        # Animated card dimensions.
        self._w = float(IDLE_W)
        self._h = float(IDLE_H)
        self._end = (self._w, self._h)

        self._card = QRectF()
        self._gcard = QRectF()

        self.anim = QVariantAnimation(self)
        self.anim.valueChanged.connect(self._on_anim)
        self.anim.finished.connect(self._on_animation_finished)

        self.apply_geometry()

        # Screen geometry changes.
        screen = QApplication.primaryScreen()
        screen.geometryChanged.connect(self.apply_geometry)
        screen.availableGeometryChanged.connect(self.apply_geometry)

        # Timers.
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.poll_cursor)
        self.poll_timer.start(30)

        self.tick_timer = QTimer(self)
        self.tick_timer.timeout.connect(self.update)

        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self._on_clock)
        self.clock_timer.start(1000)

        self.fs_timer = QTimer(self)
        self.fs_timer.timeout.connect(self.check_fullscreen)
        self.fs_timer.start(1500)

        # System worker (battery, Wi-Fi, Bluetooth, media).
        self.worker = SystemWorker()
        self.worker.status.connect(self.on_status)
        self.worker.media.connect(self.on_media)

        # Weather worker.
        self.wx_worker = WeatherWorker(self.city, self.unit)
        self.wx_worker.updated.connect(self.on_weather)
        self.wx_worker.failed.connect(self.on_weather_failed)

        # Performance monitor (CPU, RAM, disk, network).
        self.start_monitoring()

        # Charging live activity.
        self.init_charging()

        # Voice assistant.
        self.init_assistant()

        # Context menu.
        self.context_menu = ContextMenuController(
            self,
            startup_enabled,
            set_startup,
        )

    # ------------------------------------------------------------------ #
    # Performance monitor
    # ------------------------------------------------------------------ #

    def start_monitoring(self):
        if not self.monitor_available:
            return

        self.monitor_thread = QThread(self)
        self.monitor_worker = SystemMonitorWorker(interval=1000)
        self.monitor_worker.moveToThread(self.monitor_thread)

        self.monitor_thread.started.connect(self.monitor_worker.start)
        self.monitor_worker.metrics_ready.connect(self.on_metrics)
        self.monitor_worker.error_occurred.connect(self.on_metrics_error)

        self.monitor_thread.start()

    def on_metrics(self, metrics):
        self.metrics = metrics
        self.metrics_error = ""

        try:
            self.cpu_history.append(float(metrics["cpu"]["usage"]))
            self.mem_history.append(float(metrics["memory"]["usage"]))

            self.disk_history.append(
                float(metrics["disk"]["read_speed"])
                + float(metrics["disk"]["write_speed"])
            )

            self.net_history.append(
                float(metrics["network"]["download_speed"])
            )

        except (KeyError, TypeError, ValueError):
            pass

        if self.state == "expanded" and self.isVisible():
            self.update()

    def on_metrics_error(self, message):
        self.metrics_error = str(message)

        if self.state == "expanded" and self.isVisible():
            self.update()

    def net_peak(self):
        """Scale for the network graph: recent peak, at least 256 KB/s."""

        return max(max(self.net_history, default=0.0), 256 * 1024.0)

    def open_stats_view(self):
        """Switch the expanded panel to the live performance view."""

        if not self.monitor_available:
            return

        self.stats_view = True
        self.hover_item = None
        self.retarget()
        self.update()

    def close_stats_view(self):
        """Return from the performance view to the normal panel."""

        self.stats_view = False
        self.hover_item = None
        self.retarget()
        self.update()

    def open_task_manager(self):
        try:
            os.startfile("taskmgr.exe")
        except OSError:
            pass

    def shutdown(self):
        """Stop timers, the voice helpers and the monitor thread on exit."""

        for timer in (
            self.poll_timer,
            self.tick_timer,
            self.clock_timer,
            self.fs_timer,
        ):
            timer.stop()

        self.shutdown_assistant()

        thread = self.monitor_thread

        if thread is not None:
            try:
                if thread.isRunning():
                    thread.quit()

                    if not thread.wait(2000):
                        thread.terminate()
                        thread.wait(1000)

            except RuntimeError:
                pass

    # ------------------------------------------------------------------ #
    # Win32: click-through while idle
    # ------------------------------------------------------------------ #

    def showEvent(self, event):
        super().showEvent(event)
        set_click_through(self, self.state == "idle")

    def check_fullscreen(self):
        busy = fullscreen_app_running()

        if busy and self.isVisible():
            if self.notification_popup.isVisible():
                self.notification_popup.close_animated()

            self.hide()

        elif not busy and not self.isVisible():
            self.show()

    def _on_clock(self):
        if self.state != "idle" and self.isVisible():
            self.retarget(160)
            self.update()

    # ------------------------------------------------------------------ #
    # Geometry and animation
    # ------------------------------------------------------------------ #

    def apply_geometry(self, *_):
        screen = QApplication.primaryScreen()
        geometry = screen.geometry()

        w, h = self._w, self._h

        if self.anchor == "top-right":
            sx = geometry.right() + 1 - SIDE_MARGIN - w

        elif self.anchor == "top-left":
            sx = geometry.left() + SIDE_MARGIN

        else:
            sx = geometry.left() + (geometry.width() - w) / 2

        top = geometry.top() + TOP_MARGIN

        wx = math.floor(sx) - PAD_X
        wy = top - PAD_T

        ww = math.ceil(w) + 2 * PAD_X + 1
        wh = math.ceil(h) + PAD_T + PAD_B + 1

        self._card = QRectF(sx - wx, PAD_T, w, h)
        self._gcard = QRectF(sx, top, w, h)

        if (self.x(), self.y(), self.width(), self.height()) != (wx, wy, ww, wh):
            self.setGeometry(wx, wy, ww, wh)

        self.update()

    def is_playing(self):
        return bool(self.media and self.media["playing"])

    def hover_height(self):
        if self.assistant_active:
            return int(self.assistant_size()[1])

        return PILL_H + (HOVER_MEDIA_H if self.media else 0)

    def exp_height(self):
        if self.stats_view:
            return STATS_VIEW_H

        return EXP_H_MEDIA if self.media else EXP_H_BASE

    def compact_width(self):
        return 32 + self._status_row(None, 0, 0, 0)

    def target_size(self):
        if self.assistant_active and self.state != "expanded":
            return self.assistant_size()

        if self.state == "idle" and self.charge_activity:
            return self.charge_size()

        if self.state == "expanded":
            return float(EXP_W), float(self.exp_height())

        if self.state == "hover":
            width = self.compact_width()

            if self.media:
                width = max(width, HOVER_MEDIA_W)

            return float(width), float(self.hover_height())

        return float(IDLE_W), float(IDLE_H)

    def retarget(self, duration=None):
        end = self.target_size()

        if end == self._end:
            return

        self._end = end
        self.anim.stop()

        if duration is None:
            duration = 380 if self.state == "expanded" else 260

        self.anim.setDuration(duration)

        if self.state == "expanded":
            curve = QEasingCurve.Type.OutQuart
        elif self.state == "idle" and (
            self.charge_activity or self.assistant_active
        ):
            curve = QEasingCurve.Type.OutBack   # small bounce on pop-out
        else:
            curve = QEasingCurve.Type.OutCubic

        self.anim.setEasingCurve(curve)

        self.anim.setStartValue(QRectF(0, 0, self._w, self._h))
        self.anim.setEndValue(QRectF(0, 0, end[0], end[1]))
        self.anim.start()

    def _on_anim(self, value):
        self._w = value.width()
        self._h = value.height()
        self.apply_geometry()

    def _on_animation_finished(self):
        if not self._open_notification_popup:
            return

        self._open_notification_popup = False

        if self.state == "hover" and self.isVisible():
            self.notification_popup.show_below(self._gcard.toRect())
            self._left_at = None

    def set_state(self, state):
        if state == self.state:
            return

        self.state = state
        self._left_at = None
        self._enter_at = None

        if state != "expanded":
            self.stats_view = False

        if state != "idle":
            self.end_charge_activity(immediate=True)

        if state == "expanded":
            self.hide_assistant(immediate=True)

        if state == "idle":
            self._open_notification_popup = False

            if self.notification_popup.isVisible():
                self.notification_popup.close_animated()

            self.hover_item = None
            self.notification_view = False
            self.setCursor(Qt.CursorShape.ArrowCursor)

        set_click_through(self, state == "idle")

        self._sync_tick()
        self.retarget()
        self.update()

    def _sync_tick(self):
        if self.is_playing():
            self.tick_timer.setInterval(40 if self.state != "idle" else 90)

            if not self.tick_timer.isActive():
                self.tick_timer.start()

        else:
            self.tick_timer.stop()

    # ------------------------------------------------------------------ #
    # Data from workers
    # ------------------------------------------------------------------ #

    def on_status(self, status):
        self.battery = status["battery"]

        if time.perf_counter() >= self._radio_lock_until:
            self.wifi_text = status["wifi_text"]
            self.wifi_connected = status["wifi_connected"]
            self.wifi_on = status["wifi_on"]
            self.bt = status["bt"]

        dark = system_uses_dark()

        self.dark = dark
        self.th = make_theme(dark)
        self.accent = system_accent(dark)

        self.retarget(200)
        self.update()

    def on_weather(self, weather):
        self.weather = weather
        self.retarget(200)
        self.update()

    def on_weather_failed(self):
        if self.weather is None:
            self.wx_msg = "Weather unavailable"
            self.update()

    def on_media(self, info):
        if info is None:
            self.media = None
            self.art = None

        else:
            art = info.pop("art")

            if art is not False:
                self._set_art(art)

            if time.perf_counter() < self._ignore_play_until and self.media:
                info["playing"] = self.media["playing"]

            self.media = info
            self.media_t = time.perf_counter()

        self._sync_tick()
        self.retarget()
        self.update()

    def _set_art(self, data):
        from PySide6.QtGui import QPixmap

        self.art = None

        if data:
            pixmap = QPixmap()

            if pixmap.loadFromData(data) and not pixmap.isNull():
                self.art = pixmap

                side = min(pixmap.width(), pixmap.height())

                self._art_src = QRectF(
                    (pixmap.width() - side) / 2,
                    (pixmap.height() - side) / 2,
                    side,
                    side,
                )

    def cur_pos(self):
        media = self.media
        pos = media["position"]

        if media["playing"]:
            pos += time.perf_counter() - self.media_t

        return min(pos, media["duration"]) if media["duration"] else pos

    # ------------------------------------------------------------------ #
    # Actions
    # ------------------------------------------------------------------ #

    def toggle_radio(self, kind):
        if not WINRT:
            os.startfile(SETTINGS_URI[kind])
            return

        if kind == "wifi":
            current = (
                self.wifi_on
                if self.wifi_on is not None
                else self.wifi_connected
            )

            self.wifi_on = not current

            if not self.wifi_on:
                self.wifi_connected = False

            self.wifi_text = "Off" if not self.wifi_on else "Turning on…"
            target = self.wifi_on

        else:
            if self.bt is None:
                os.startfile(SETTINGS_URI["bt"])
                return

            self.bt = not self.bt
            target = self.bt

        self._radio_lock_until = time.perf_counter() + 3.0

        self.worker.command("radio", (kind, target))
        self.update()

    def media_action(self, name):
        if self.media is None:
            return

        if name == "play":
            self.media["position"] = self.cur_pos()
            self.media_t = time.perf_counter()
            self.media["playing"] = not self.media["playing"]
            self._ignore_play_until = time.perf_counter() + 1.2

            self.worker.command("toggle")

            self._sync_tick()
            self.retarget()

        else:
            self.worker.command(name)

        self.update()

    # ------------------------------------------------------------------ #
    # Mouse handling
    # ------------------------------------------------------------------ #

    def poll_cursor(self):
        from PySide6.QtGui import QCursor

        if self._menu_open or not self.isVisible():
            return

        cur = QPointF(QCursor.pos())
        gc = self._gcard
        now = time.perf_counter()

        if self.state == "idle":
            zone = gc.adjusted(-40, -TOP_MARGIN - 4, 40, 12)

            if zone.contains(cur) and not left_button_down():
                if self._enter_at is None:
                    self._enter_at = now

                elif now - self._enter_at > 0.12:
                    self.set_state("hover")

            else:
                self._enter_at = None

            return

        zone = gc.adjusted(-6, -TOP_MARGIN - 4, 6, 8)

        if self.notification_popup.isVisible():
            popup_zone = QRectF(self.notification_popup.frameGeometry())
            zone = zone.united(popup_zone.adjusted(-6, -6, 6, 6))

        # hold_open_until keeps a voice-opened panel up while you talk.
        if zone.contains(cur) or now < self.hold_open_until:
            self._left_at = None

        else:
            if self.hover_item is not None:
                self.hover_item = None
                self.update()

            if self._left_at is None:
                self._left_at = now

            elif now - self._left_at > (
                1.0 if self.state == "expanded" else 0.3
            ):
                self.set_state("idle")

    def hover_ctrls(self, width):
        """Mini-player buttons in hover pill coordinates."""

        y = PILL_H + 7

        return {
            "prev": QRectF(width - 104, y, 30, 30),
            "play": QRectF(width - 74, y, 30, 30),
            "next": QRectF(width - 44, y, 30, 30),
        }

    def hit(self, rel):
        if self.state == "hover":
            if self.media:
                for name, rect in self.hover_ctrls(
                    self._card.width()
                ).items():
                    if rect.contains(rel):
                        return ("btn", name)

            return None

        if self.state != "expanded":
            return None

        # Performance view controls.
        if self.stats_view:
            if STATS_BACK.contains(rel):
                return ("stats_back", None)

            if STATS_TASKMGR.contains(rel):
                return ("stats_taskmgr", None)

            return None

        # Notification bell.
        if QRectF(EXP_W - 44, 8, 36, 34).contains(rel):
            return ("notifications", None)

        # Quick setting tiles.
        for i in range(3):
            tile = QRectF(
                16 + i * (TILE_W + TILE_GAP),
                TILES_Y,
                TILE_W,
                TILE_H,
            )

            if tile.contains(rel):
                return ("tile", i)

        # System stats cells.
        if self.monitor_available:
            for i in range(STAT_COUNT):
                if self.stat_rect(i).contains(rel):
                    return ("stat", i)

        # Volume controls.
        for name, rect in self.vol_rects().items():
            if rect.contains(rel):
                return ("vol", name)

        # Media controls.
        if self.media:
            for name, rect in CTRL.items():
                if rect.contains(rel):
                    return ("btn", name)

            if self.media["duration"] and PROGRESS_HIT.contains(rel):
                return ("seek", None)

        return None

    def mouseMoveEvent(self, event):
        item = self.hit(event.position() - self._card.topLeft())

        if item != self.hover_item:
            self.hover_item = item

            self.setCursor(
                Qt.CursorShape.PointingHandCursor
                if item and item != ("tile", 2)
                else Qt.CursorShape.ArrowCursor
            )

            self.update()

    def mousePressEvent(self, event):
        pos = event.position()

        if not self._card.contains(pos):
            return

        rel = pos - self._card.topLeft()
        item = self.hit(rel)

        if event.button() == Qt.MouseButton.RightButton:
            if item and item[0] == "tile" and item[1] in (0, 1):
                os.startfile(SETTINGS_URI[("wifi", "bt")[item[1]]])
            else:
                self.context_menu.show(event.globalPosition().toPoint())

            return

        if event.button() != Qt.MouseButton.LeftButton:
            return

        if self.state == "hover":
            if item and item[0] == "btn":
                self.media_action(item[1])
            else:
                self.set_state("expanded")

            return

        if self.state != "expanded":
            return

        # Performance view: only the header buttons react.
        if self.stats_view:
            if item == ("stats_back", None):
                self.close_stats_view()

            elif item == ("stats_taskmgr", None):
                self.open_task_manager()

            return

        if item is None:
            self.set_state("hover")
            return

        kind = item[0]

        if kind == "notifications":
            self._open_notification_popup = True
            self.set_state("hover")

        elif kind == "tile":
            if item[1] == 0:
                self.toggle_radio("wifi")

            elif item[1] == 1:
                self.toggle_radio("bt")

        elif kind == "stat":
            self.open_stats_view()

        elif kind == "vol":
            tap_key(VK_VOLUME[item[1]])

        elif kind == "btn":
            self.media_action(item[1])

        elif kind == "seek":
            frac = clamp((rel.x() - 16) / (EXP_W - 32))
            target = frac * self.media["duration"]

            self.media["position"] = target
            self.media_t = time.perf_counter()

            self.worker.command("seek", target)
            self.update()


# --------------------------------------------------------------------------- #
# Application entry point
# --------------------------------------------------------------------------- #

def main():
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    glance = WindowsGlance()
    app.aboutToQuit.connect(glance.shutdown)
    glance.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()