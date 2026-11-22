"""
Windows Glance  -  a Windows 11 style status pill with weather, quick toggles and a media widget.

Install (Python 3.9 - 3.12):
    pip install PySide6 winsdk

Python 3.13+ (winsdk has no wheels there), use the modular winrt packages instead:
    pip install PySide6 winrt-runtime winrt-Windows.Foundation winrt-Windows.Foundation.Collections ^
        winrt-Windows.Media.Control winrt-Windows.Storage.Streams winrt-Windows.Devices.Radios winrt-Windows.Networking.Connectivity

Without winsdk/winrt the app still runs (battery, Wi-Fi name, clock, weather), but Bluetooth,
the Wi-Fi/Bluetooth toggles and the media widget are unavailable.

States
    idle      : tiny see-through sliver at the top of the screen (click-through, never blocks apps).
                While music plays, little equalizer bars dance inside it.
    hover     : cursor over it -> compact pill: clock, weather, Wi-Fi, Bluetooth, battery.
                When media is playing a mini player appears under it (prev / play-pause / next).
    expanded  : click the pill -> full panel: weather + clock, Wi-Fi / Bluetooth toggles,
                battery, full media player (seek bar) and volume buttons.

Controls
    Wi-Fi / Bluetooth tiles : click = turn on / off right here.  Right-click = open Windows Settings.
    Media                   : prev / play-pause / next, click the progress bar to seek.
    Volume                  : mute, volume down, volume up.
    Right-click (elsewhere) : menu (position, weather units / location, run at startup, exit).

Weather uses the free Open-Meteo API (no key). Location is detected from your IP;
use the menu -> "Weather location..." to pick a city yourself.
"""

import asyncio
import ctypes
import importlib
import json
import math
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
import winreg

from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime, timezone

from PySide6.QtCore import (
    QDate,
    QEasingCurve,
    QLocale,
    QObject,
    QPointF,
    QRect,
    QRectF,
    QSettings,
    Qt,
    QTime,
    QTimer,
    QVariantAnimation,
    Signal,
)

from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QBrush,
    QColor,
    QCursor,
    QFont,
    QFontMetricsF,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)

from PySide6.QtWidgets import (
    QApplication,
    QInputDialog,
    QMenu,
    QWidget,
)

from core.weather_worker import WeatherWorker
from core.system_worker import SystemWorker

from ui.context_menu import ContextMenuController


# --------------------------------------------------------------------------- #
# Layout constants
# --------------------------------------------------------------------------- #

IDLE_W, IDLE_H = 46, 10
PILL_H = 34
HOVER_MEDIA_H = 44
HOVER_MEDIA_W = 336
EXP_W = 360

TILES_Y = 76
TILE_W, TILE_H, TILE_GAP = 104, 64, 8
MEDIA_Y = 156
EXP_H_BASE = MEDIA_Y + 40
EXP_H_MEDIA = MEDIA_Y + 124

PAD_X, PAD_T, PAD_B = 12, 8, 16
TOP_MARGIN = 8
SIDE_MARGIN = 16

R_IDLE, R_COMPACT, R_PANEL = 4.0, 9.0, 10.0

CTRL = {
    "prev": QRectF(88, MEDIA_Y + 38, 32, 32),
    "play": QRectF(124, MEDIA_Y + 38, 32, 32),
    "next": QRectF(160, MEDIA_Y + 38, 32, 32),
}

PROGRESS_HIT = QRectF(
    16,
    MEDIA_Y + 72,
    EXP_W - 32,
    16,
)

SETTINGS_URI = {
    "wifi": "ms-settings:network-wifi",
    "bt": "ms-settings:bluetooth",
    "battery": "ms-settings:batterysaver",
}


# --------------------------------------------------------------------------- #
# Win32 helpers
# --------------------------------------------------------------------------- #

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020

_user32 = ctypes.windll.user32

_get_wl = (
    getattr(_user32, "GetWindowLongPtrW", None)
    or _user32.GetWindowLongW
)

_set_wl = (
    getattr(_user32, "SetWindowLongPtrW", None)
    or _user32.SetWindowLongW
)

_get_wl.restype = ctypes.c_ssize_t
_get_wl.argtypes = [wintypes.HWND, ctypes.c_int]

_set_wl.restype = ctypes.c_ssize_t
_set_wl.argtypes = [
    wintypes.HWND,
    ctypes.c_int,
    ctypes.c_ssize_t,
]

VK_VOLUME = {
    "mute": 0xAD,
    "vdn": 0xAE,
    "vup": 0xAF,
}


def _detect_winrt():
    """True if either the winsdk or the modular winrt packages are installed."""

    for mod in (
        "winsdk",
        "winrt.windows.devices.radios",
    ):
        try:
            importlib.import_module(mod)
            return True
        except Exception:
            continue

    return False


WINRT = _detect_winrt()


def tap_key(vk):
    """Press and release a media key."""

    _user32.keybd_event(vk, 0, 0, 0)
    _user32.keybd_event(vk, 0, 2, 0)


def left_button_down():
    return bool(
        _user32.GetAsyncKeyState(0x01) & 0x8000
    )


def fullscreen_app_running():
    """True while a full-screen app, D3D game or presentation owns the screen."""

    try:
        state = ctypes.c_int()

        result = ctypes.windll.shell32.SHQueryUserNotificationState(
            ctypes.byref(state)
        )

        if result == 0:
            return state.value in (2, 3, 4)

    except Exception:
        pass

    return False


# --------------------------------------------------------------------------- #
# General helpers
# --------------------------------------------------------------------------- #

def clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


def fmt_time(sec):
    sec = int(max(0, sec))
    return f"{sec // 60}:{sec % 60:02d}"


_fonts = {}


def ui_font(px, weight=QFont.Weight.Normal):
    key = ("ui", px, weight)

    if key not in _fonts:
        font = QFont()
        font.setFamilies([
            "Segoe UI Variable Text",
            "Segoe UI",
            "Arial",
        ])
        font.setPixelSize(px)
        font.setWeight(weight)

        _fonts[key] = font

    return _fonts[key]


def icon_font(px):
    key = ("icon", px)

    if key not in _fonts:
        font = QFont()
        font.setFamilies([
            "Segoe Fluent Icons",
            "Segoe MDL2 Assets",
        ])
        font.setPixelSize(px)

        _fonts[key] = font

    return _fonts[key]


def draw_glyph(p, glyph, rect, px, color):
    p.setFont(icon_font(px))
    p.setPen(color)
    p.drawText(
        rect,
        Qt.AlignmentFlag.AlignCenter,
        glyph,
    )


def draw_text(p, s, x, y, font, color, max_w=None):
    if max_w is not None:
        s = QFontMetricsF(font).elidedText(
            s,
            Qt.TextElideMode.ElideRight,
            max(8, max_w),
        )

    p.setFont(font)
    p.setPen(color)
    p.drawText(QPointF(x, y), s)


def contrast(c):
    lum = (
        0.299 * c.red()
        + 0.587 * c.green()
        + 0.114 * c.blue()
    )

    return (
        QColor(0, 0, 0, 230)
        if lum > 150
        else QColor(255, 255, 255)
    )


def with_alpha(c, a):
    return QColor(
        c.red(),
        c.green(),
        c.blue(),
        a,
    )


def time_text():
    return QLocale.system().toString(
        QTime.currentTime(),
        QLocale.FormatType.ShortFormat,
    )


def date_text():
    return QLocale.system().toString(
        QDate.currentDate(),
        "ddd, d MMM",
    )


# --------------------------------------------------------------------------- #
# Weather icons
# --------------------------------------------------------------------------- #

def wx_text(code):
    if code == 0:
        return "Clear"

    if code == 1:
        return "Mostly clear"

    if code == 2:
        return "Partly cloudy"

    if code == 3:
        return "Overcast"

    if code in (45, 48):
        return "Foggy"

    if 51 <= code <= 57:
        return "Drizzle"

    if 61 <= code <= 67:
        return "Rain"

    if 71 <= code <= 77 or code in (85, 86):
        return "Snow"

    if 80 <= code <= 82:
        return "Showers"

    if code >= 95:
        return "Thunderstorm"

    return "Cloudy"


def draw_weather(p, code, is_day, rect, dark):
    x, y = rect.left(), rect.top()
    s = min(rect.width(), rect.height())

    cloud_col = (
        QColor(228, 233, 243)
        if dark
        else QColor(128, 140, 158)
    )

    sun_col = QColor("#FFC83D")

    moon_col = (
        QColor(214, 222, 255)
        if dark
        else QColor(118, 130, 176)
    )

    rain_col = QColor("#4DB1FF")
    lw = max(1.0, s * 0.07)

    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    def sun(cx, cy, r):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(sun_col)
        p.drawEllipse(QPointF(cx, cy), r, r)

        pen = QPen(sun_col, lw)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)

        for i in range(8):
            a = i * math.pi / 4

            p.drawLine(
                QPointF(
                    cx + math.cos(a) * r * 1.5,
                    cy + math.sin(a) * r * 1.5,
                ),
                QPointF(
                    cx + math.cos(a) * r * 1.9,
                    cy + math.sin(a) * r * 1.9,
                ),
            )

    def moon(cx, cy, r):
        a = QPainterPath()
        a.addEllipse(QPointF(cx, cy), r, r)

        b = QPainterPath()
        b.addEllipse(
            QPointF(
                cx + r * 0.55,
                cy - r * 0.35,
            ),
            r * 0.85,
            r * 0.85,
        )

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(moon_col)
        p.drawPath(a.subtracted(b))

    def cloud(ox, oy, k):
        b = s * k
        cx, cy = x + ox * s, y + oy * s

        path = QPainterPath()

        path.addEllipse(
            QPointF(cx + .30 * b, cy + .62 * b),
            .20 * b,
            .20 * b,
        )

        path.addEllipse(
            QPointF(cx + .50 * b, cy + .48 * b),
            .26 * b,
            .26 * b,
        )

        path.addEllipse(
            QPointF(cx + .74 * b, cy + .64 * b),
            .18 * b,
            .18 * b,
        )

        path.addRoundedRect(
            QRectF(
                cx + .30 * b,
                cy + .62 * b,
                .44 * b,
                .20 * b,
            ),
            .1 * b,
            .1 * b,
        )

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(cloud_col)
        p.drawPath(path.simplified())

    def lines(col, ys, slant=0.0):
        pen = QPen(col, lw)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)

        for lx, ly1, ly2 in ys:
            p.drawLine(
                QPointF(
                    x + lx * s,
                    y + ly1 * s,
                ),
                QPointF(
                    x + (lx - slant) * s,
                    y + ly2 * s,
                ),
            )

    if code in (0, 1):
        if is_day:
            sun(
                x + .5 * s,
                y + .5 * s,
                .2 * s,
            )
        else:
            moon(
                x + .5 * s,
                y + .5 * s,
                .3 * s,
            )

    elif code == 2:
        if is_day:
            sun(
                x + .36 * s,
                y + .36 * s,
                .15 * s,
            )
        else:
            moon(
                x + .38 * s,
                y + .36 * s,
                .2 * s,
            )

        cloud(.12, .22, .88)

    elif code in (45, 48):
        cloud(.0, -.04, 1.0)
        lines(
            cloud_col,
            [
                (.28, .82, .82),
                (.5, .92, .92),
            ],
        )

    elif code in (
        51, 53, 55, 56, 57,
        61, 63, 65, 66, 67,
        80, 81, 82,
    ):
        cloud(.05, -.04, .9)

        lines(
            rain_col,
            [
                (.34, .76, .94),
                (.54, .76, .94),
                (.74, .76, .94),
            ],
            slant=.06,
        )

    elif 71 <= code <= 77 or code in (85, 86):
        cloud(.05, -.04, .9)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(
            QColor(255, 255, 255)
            if dark
            else QColor(110, 150, 200)
        )

        for lx in (.32, .52, .72):
            p.drawEllipse(
                QPointF(
                    x + lx * s,
                    y + .88 * s,
                ),
                .05 * s,
                .05 * s,
            )

    elif code >= 95:
        cloud(.05, -.1, .9)

        poly = QPolygonF([
            QPointF(x + a * s, y + b * s)
            for a, b in (
                (.55, .6),
                (.38, .84),
                (.50, .84),
                (.44, 1.0),
                (.64, .72),
                (.52, .72),
            )
        ])

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(sun_col)
        p.drawPolygon(poly)

    else:
        cloud(.0, .1, 1.0)

    p.restore()


# --------------------------------------------------------------------------- #
# Theme and accent colour
# --------------------------------------------------------------------------- #

@dataclass
class Theme:
    dark: bool
    bg: QColor
    sheen: QColor
    border_hi: QColor
    border_lo: QColor
    text: QColor
    sub: QColor
    tile: QColor
    tile_hover: QColor
    stroke: QColor
    hover_overlay: QColor
    good: QColor
    bad: QColor
    shadow: int


def make_theme(dark):
    if dark:
        return Theme(
            dark=True,
            bg=QColor(30, 30, 32, 242),
            sheen=QColor(255, 255, 255, 12),
            border_hi=QColor(255, 255, 255, 56),
            border_lo=QColor(255, 255, 255, 16),
            text=QColor(255, 255, 255),
            sub=QColor(255, 255, 255, 160),
            tile=QColor(255, 255, 255, 14),
            tile_hover=QColor(255, 255, 255, 26),
            stroke=QColor(255, 255, 255, 14),
            hover_overlay=QColor(255, 255, 255, 10),
            good=QColor("#6CCB5F"),
            bad=QColor("#FF99A4"),
            shadow=12,
        )

    return Theme(
        dark=False,
        bg=QColor(248, 248, 249, 246),
        sheen=QColor(255, 255, 255, 140),
        border_hi=QColor(0, 0, 0, 22),
        border_lo=QColor(0, 0, 0, 52),
        text=QColor(0, 0, 0, 228),
        sub=QColor(0, 0, 0, 150),
        tile=QColor(255, 255, 255, 170),
        tile_hover=QColor(255, 255, 255, 235),
        stroke=QColor(0, 0, 0, 15),
        hover_overlay=QColor(0, 0, 0, 9),
        good=QColor("#0F7B0F"),
        bad=QColor("#C42B1C"),
        shadow=8,
    )


def system_uses_dark():
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            return (
                winreg.QueryValueEx(
                    key,
                    "SystemUsesLightTheme",
                )[0] == 0
            )

    except OSError:
        return True


def system_accent(dark):
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Accent",
        ) as key:
            value = winreg.QueryValueEx(
                key,
                "AccentColorMenu",
            )[0]

        colour = QColor(
            value & 0xFF,
            (value >> 8) & 0xFF,
            (value >> 16) & 0xFF,
        )

        return (
            colour.lighter(118)
            if dark
            else colour
        )

    except OSError:
        return (
            QColor("#60CDFF")
            if dark
            else QColor("#005FB8")
        )


# --------------------------------------------------------------------------- #
# Run at startup
# --------------------------------------------------------------------------- #

RUN_KEY = (
    r"Software\Microsoft\Windows\CurrentVersion\Run"
)


def startup_enabled():
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            RUN_KEY,
        ) as key:
            winreg.QueryValueEx(
                key,
                "WindowsGlance",
            )

            return True

    except OSError:
        return False


def set_startup(enabled):
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            RUN_KEY,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:

            if enabled:
                exe = sys.executable

                pyw = exe.replace(
                    "python.exe",
                    "pythonw.exe",
                )

                if os.path.exists(pyw):
                    exe = pyw

                winreg.SetValueEx(
                    key,
                    "WindowsGlance",
                    0,
                    winreg.REG_SZ,
                    f'"{exe}" "{os.path.abspath(__file__)}"',
                )

            else:
                try:
                    winreg.DeleteValue(
                        key,
                        "WindowsGlance",
                    )
                except OSError:
                    pass

    except OSError:
        pass


# --------------------------------------------------------------------------- #
# Supporting application components
# --------------------------------------------------------------------------- #

from core.notification_store import NotificationStore
from ui.painter import PainterMixin
from ui.notification_popup import NotificationPopup


# --------------------------------------------------------------------------- #
# Main widget
# --------------------------------------------------------------------------- #

class WindowsGlance(PainterMixin, QWidget):

    ANCHORS = (
        ("top-center", "Top center"),
        ("top-right", "Top right"),
        ("top-left", "Top left"),
    )

    def __init__(self):
        super().__init__()

        self.settings = QSettings(
            "WindowsGlance",
            "Glance",
        )

        self.anchor = self.settings.value(
            "anchor",
            "top-center",
        )

        if self.anchor not in dict(self.ANCHORS):
            self.anchor = "top-center"

        self.unit = self.settings.value(
            "wx_unit",
            "C",
        )

        if self.unit not in ("C", "F"):
            self.unit = "C"

        self.city = str(
            self.settings.value("wx_city", "") or ""
        )

        # Notifications.
        self.notifications = NotificationStore()
        self.notifications.changed.connect(self.update)
        self.notification_view = False

        # Interface state.
        self.state = "idle"
        self.hover_item = None

        self._left_at = None
        self._enter_at = None
        self._menu_open = False
        self._radio_lock_until = 0.0

        self.battery = None

        self.wifi_text = "Checking…"
        self.wifi_connected = False
        self.wifi_on = None

        self.bt = None

        self.weather = None
        self.wx_msg = "Loading weather…"

        self.media = None
        self.media_t = 0.0

        self.art = None
        self._art_src = QRectF()

        self._ignore_play_until = 0.0

        self.dark = system_uses_dark()
        self.th = make_theme(self.dark)
        self.accent = system_accent(self.dark)

        self.notification_popup = NotificationPopup(
            self.notifications,
            self,
        )

        self._open_notification_popup = False

        # Window configuration.
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )

        self.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground
        )

        self.setAttribute(
            Qt.WidgetAttribute.WA_ShowWithoutActivating
        )

        self.setMouseTracking(True)

        # Animated card dimensions.
        self._w = float(IDLE_W)
        self._h = float(IDLE_H)

        self._end = (
            self._w,
            self._h,
        )

        self._card = QRectF()
        self._gcard = QRectF()

        self.anim = QVariantAnimation(self)
        self.anim.valueChanged.connect(self._on_anim)
        self.anim.finished.connect(
            self._on_animation_finished
        )

        self.apply_geometry()

        # Screen geometry changes.
        screen = QApplication.primaryScreen()

        screen.geometryChanged.connect(
            self.apply_geometry
        )

        screen.availableGeometryChanged.connect(
            self.apply_geometry
        )

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
        self.fs_timer.timeout.connect(
            self.check_fullscreen
        )
        self.fs_timer.start(1500)

        # System worker.
        self.worker = SystemWorker()
        self.worker.status.connect(self.on_status)
        self.worker.media.connect(self.on_media)

        # Weather worker.
        self.wx_worker = WeatherWorker(
            self.city,
            self.unit,
        )

        self.wx_worker.updated.connect(self.on_weather)
        self.wx_worker.failed.connect(
            self.on_weather_failed
        )

        # Context menu.
        self.context_menu = ContextMenuController(
            self,
            startup_enabled,
            set_startup,
        )

    # ------------------------------------------------------------------ #
    # Win32: click-through while idle
    # ------------------------------------------------------------------ #

    def _set_click_through(self, on):
        try:
            hwnd = wintypes.HWND(int(self.winId()))

            style = _get_wl(
                hwnd,
                GWL_EXSTYLE,
            )

            new_style = (
                style | WS_EX_TRANSPARENT
                if on
                else style & ~WS_EX_TRANSPARENT
            )

            if new_style != style:
                _set_wl(
                    hwnd,
                    GWL_EXSTYLE,
                    new_style,
                )

        except Exception:
            pass

    def showEvent(self, event):
        super().showEvent(event)

        self._set_click_through(
            self.state == "idle"
        )

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
            sx = (
                geometry.right()
                + 1
                - SIDE_MARGIN
                - w
            )

        elif self.anchor == "top-left":
            sx = geometry.left() + SIDE_MARGIN

        else:
            sx = (
                geometry.left()
                + (geometry.width() - w) / 2
            )

        top = geometry.top() + TOP_MARGIN

        wx = math.floor(sx) - PAD_X
        wy = top - PAD_T

        ww = math.ceil(w) + 2 * PAD_X + 1
        wh = math.ceil(h) + PAD_T + PAD_B + 1

        self._card = QRectF(
            sx - wx,
            PAD_T,
            w,
            h,
        )

        self._gcard = QRectF(
            sx,
            top,
            w,
            h,
        )

        if (
            self.x(),
            self.y(),
            self.width(),
            self.height(),
        ) != (
            wx,
            wy,
            ww,
            wh,
        ):
            self.setGeometry(
                wx,
                wy,
                ww,
                wh,
            )

        self.update()

    def is_playing(self):
        return bool(
            self.media and self.media["playing"]
        )

    def hover_height(self):
        return (
            PILL_H
            + (
                HOVER_MEDIA_H
                if self.media
                else 0
            )
        )

    def exp_height(self):
        return (
            EXP_H_MEDIA
            if self.media
            else EXP_H_BASE
        )

    def compact_width(self):
        return 32 + self._status_row(
            None,
            0,
            0,
            0,
        )

    def target_size(self):
        if self.state == "expanded":
            return (
                float(EXP_W),
                float(self.exp_height()),
            )

        if self.state == "hover":
            width = self.compact_width()

            if self.media:
                width = max(
                    width,
                    HOVER_MEDIA_W,
                )

            return (
                float(width),
                float(self.hover_height()),
            )

        return (
            float(IDLE_W),
            float(IDLE_H),
        )

    def retarget(self, duration=None):
        end = self.target_size()

        if end == self._end:
            return

        self._end = end
        self.anim.stop()

        if duration is None:
            duration = (
                380
                if self.state == "expanded"
                else 260
            )

        self.anim.setDuration(duration)

        self.anim.setEasingCurve(
            QEasingCurve.Type.OutQuart
            if self.state == "expanded"
            else QEasingCurve.Type.OutCubic
        )

        self.anim.setStartValue(
            QRectF(
                0,
                0,
                self._w,
                self._h,
            )
        )

        self.anim.setEndValue(
            QRectF(
                0,
                0,
                end[0],
                end[1],
            )
        )

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
            self.notification_popup.show_below(
                self._gcard.toRect()
            )

            self._left_at = None

    def set_state(self, state):
        if state == self.state:
            return

        self.state = state
        self._left_at = None
        self._enter_at = None

        if state == "idle":
            self._open_notification_popup = False

            if self.notification_popup.isVisible():
                self.notification_popup.close_animated()

            self.hover_item = None
            self.notification_view = False

            self.setCursor(
                Qt.CursorShape.ArrowCursor
            )

        self._set_click_through(
            state == "idle"
        )

        self._sync_tick()
        self.retarget()
        self.update()

    def _sync_tick(self):
        if self.is_playing():
            self.tick_timer.setInterval(
                40
                if self.state != "idle"
                else 90
            )

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

            if (
                time.perf_counter() < self._ignore_play_until
                and self.media
            ):
                info["playing"] = self.media["playing"]

            self.media = info
            self.media_t = time.perf_counter()

        self._sync_tick()
        self.retarget()
        self.update()

    def _set_art(self, data):
        self.art = None

        if data:
            pixmap = QPixmap()

            if (
                pixmap.loadFromData(data)
                and not pixmap.isNull()
            ):
                self.art = pixmap

                side = min(
                    pixmap.width(),
                    pixmap.height(),
                )

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
            pos += (
                time.perf_counter()
                - self.media_t
            )

        return (
            min(pos, media["duration"])
            if media["duration"]
            else pos
        )

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

            self.wifi_connected = (
                False
                if not self.wifi_on
                else self.wifi_connected
            )

            self.wifi_text = (
                "Off"
                if not self.wifi_on
                else "Turning on…"
            )

            target = self.wifi_on

        else:
            if self.bt is None:
                os.startfile(SETTINGS_URI["bt"])
                return

            self.bt = not self.bt
            target = self.bt

        self._radio_lock_until = (
            time.perf_counter() + 3.0
        )

        self.worker.command(
            "radio",
            (kind, target),
        )

        self.update()

    def media_action(self, name):
        if name == "play":
            self.media["position"] = self.cur_pos()
            self.media_t = time.perf_counter()

            self.media["playing"] = (
                not self.media["playing"]
            )

            self._ignore_play_until = (
                time.perf_counter() + 1.2
            )

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
        if self._menu_open or not self.isVisible():
            return

        cur = QPointF(QCursor.pos())
        gc = self._gcard
        now = time.perf_counter()

        if self.state == "idle":
            zone = gc.adjusted(
                -40,
                -TOP_MARGIN - 4,
                40,
                12,
            )

            if zone.contains(cur) and not left_button_down():
                if self._enter_at is None:
                    self._enter_at = now

                elif now - self._enter_at > 0.12:
                    self.set_state("hover")

            else:
                self._enter_at = None

            return

        zone = gc.adjusted(
            -6,
            -TOP_MARGIN - 4,
            6,
            8,
        )

        if self.notification_popup.isVisible():
            popup_zone = QRectF(
                self.notification_popup.frameGeometry()
            )

            zone = zone.united(
                popup_zone.adjusted(
                    -6,
                    -6,
                    6,
                    6,
                )
            )

        if zone.contains(cur):
            self._left_at = None

        else:
            if self.hover_item is not None:
                self.hover_item = None
                self.update()

            if self._left_at is None:
                self._left_at = now

            elif now - self._left_at > (
                1.0
                if self.state == "expanded"
                else 0.3
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

        # Notification centre controls.
        if self.notification_view:
            if QRectF(12, 12, 32, 32).contains(rel):
                return ("notification_back", None)

            if QRectF(205, 12, 76, 32).contains(rel):
                return ("notification_read_all", None)

            if QRectF(285, 12, 64, 32).contains(rel):
                return ("notification_clear", None)

            for i, item in enumerate(
                self.notifications.get_all()[:4]
            ):
                y = 62 + i * 30

                if QRectF(310, y, 38, 28).contains(rel):
                    return (
                        "notification_dismiss",
                        item["id"],
                    )

                if QRectF(12, y, 336, 28).contains(rel):
                    return (
                        "notification_read",
                        item["id"],
                    )

            return None

        # Notification bell hitbox.
        # Its position now matches the visible bell rectangle
        # in ui/painter.py.
        if QRect(
            EXP_W - 44,
            8,
            36,
            34,
        ).contains(rel.toPoint()):
            return ("notifications", None)

        # Quick setting tiles.
        for i in range(3):
            if QRectF(
                16 + i * (TILE_W + TILE_GAP),
                TILES_Y,
                TILE_W,
                TILE_H,
            ).contains(rel):
                return ("tile", i)

        # Volume controls.
        for name, rect in self.vol_rects().items():
            if rect.contains(rel):
                return ("vol", name)

        # Media controls.
        if self.media:
            for name, rect in CTRL.items():
                if rect.contains(rel):
                    return ("btn", name)

            if (
                self.media["duration"]
                and PROGRESS_HIT.contains(rel)
            ):
                return ("seek", None)

        return None

    def mouseMoveEvent(self, event):
        item = self.hit(
            event.position() - self._card.topLeft()
        )

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
            if (
                item
                and item[0] == "tile"
                and item[1] in (0, 1)
            ):
                os.startfile(
                    SETTINGS_URI[
                        ("wifi", "bt")[item[1]]
                    ]
                )

            else:
                self.show_menu(
                    event.globalPosition().toPoint()
                )

            return

        if event.button() != Qt.MouseButton.LeftButton:
            return

        if self.state == "hover":
            if item and item[0] == "btn":
                self.media_action(item[1])

            else:
                self.set_state("expanded")

            return

        # Notification centre interactions.
        if (
            self.state == "expanded"
            and self.notification_view
        ):
            if item is None:
                return

            action, notification_id = item

            if action == "notification_back":
                self.notification_view = False

            elif action == "notification_read_all":
                self.notifications.mark_all_read()

            elif action == "notification_clear":
                self.notifications.clear()

            elif action == "notification_read":
                self.notifications.mark_read(
                    notification_id
                )

            elif action == "notification_dismiss":
                self.notifications.dismiss(
                    notification_id
                )

            self.update()
            return

        if self.state == "expanded" and item:
            if item[0] == "notifications":
                self._open_notification_popup = True
                self.set_state("hover")
                return

        if item is None:
            self.set_state("hover")

        elif item[0] == "tile":
            if item[1] == 0:
                self.toggle_radio("wifi")

            elif item[1] == 1:
                self.toggle_radio("bt")

        elif item[0] == "vol":
            tap_key(VK_VOLUME[item[1]])

        elif item[0] == "btn":
            self.media_action(item[1])

        elif item[0] == "seek":
            frac = clamp(
                (rel.x() - 16) / (EXP_W - 32)
            )

            target = frac * self.media["duration"]

            self.media["position"] = target
            self.media_t = time.perf_counter()

            self.worker.command(
                "seek",
                target,
            )

            self.update()

    # ------------------------------------------------------------------ #
    # Context menu
    # ------------------------------------------------------------------ #

    def _style_menu(self, menu):
        th = self.th

        bg = (
            f"rgb({th.bg.red()},"
            f"{th.bg.green()},"
            f"{th.bg.blue()})"
        )

        txt = (
            f"rgb({th.text.red()},"
            f"{th.text.green()},"
            f"{th.text.blue()})"
        )

        hov = (
            "rgba(255,255,255,0.09)"
            if th.dark
            else "rgba(0,0,0,0.06)"
        )

        brd = (
            "rgba(255,255,255,0.10)"
            if th.dark
            else "rgba(0,0,0,0.12)"
        )

        menu.setWindowFlags(
            menu.windowFlags()
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )

        menu.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground
        )

        menu.setStyleSheet(f"""
            QMenu {{
                background: {bg};
                color: {txt};
                border: 1px solid {brd};
                border-radius: 8px;
                padding: 4px;
                font-family: 'Segoe UI Variable Text', 'Segoe UI';
                font-size: 13px;
            }}

            QMenu::item {{
                padding: 7px 28px 7px 12px;
                border-radius: 4px;
                margin: 2px 2px;
            }}

            QMenu::item:selected {{
                background: {hov};
            }}

            QMenu::separator {{
                height: 1px;
                background: {brd};
                margin: 4px 8px;
            }}
        """)

    def show_menu(self, gpos):
        menu = QMenu(self)
        self._style_menu(menu)

        # Position submenu.
        pos_menu = menu.addMenu("Position")
        self._style_menu(pos_menu)

        group = QActionGroup(pos_menu)

        for key, label in self.ANCHORS:
            action = QAction(
                label,
                pos_menu,
                checkable=True,
            )

            action.setChecked(key == self.anchor)
            action.setData(key)

            group.addAction(action)
            pos_menu.addAction(action)

        # Weather unit submenu.
        unit_menu = menu.addMenu("Weather units")
        self._style_menu(unit_menu)

        unit_group = QActionGroup(unit_menu)

        for unit, label in (
            ("C", "Celsius (°C)"),
            ("F", "Fahrenheit (°F)"),
        ):
            action = QAction(
                label,
                unit_menu,
                checkable=True,
            )

            action.setChecked(self.unit == unit)
            action.setData("unit:" + unit)

            unit_group.addAction(action)
            unit_menu.addAction(action)

        city_action = menu.addAction(
            "Weather location…"
        )

        menu.addSeparator()

        startup = QAction(
            "Run at startup",
            menu,
            checkable=True,
        )

        startup.setChecked(startup_enabled())
        menu.addAction(startup)

        menu.addSeparator()

        quit_action = menu.addAction("Exit")

        self._menu_open = True

        try:
            chosen = menu.exec(gpos)

        finally:
            self._menu_open = False

        if chosen is None:
            return

        if chosen is quit_action:
            QApplication.quit()

        elif chosen is startup:
            set_startup(startup.isChecked())

        elif chosen is city_action:
            self.ask_city()

        else:
            data = chosen.data()

            if (
                isinstance(data, str)
                and data in dict(self.ANCHORS)
            ):
                self.anchor = data
                self.settings.setValue(
                    "anchor",
                    data,
                )
                self.apply_geometry()

            elif (
                isinstance(data, str)
                and data.startswith("unit:")
            ):
                self.unit = data[5:]

                self.settings.setValue(
                    "wx_unit",
                    self.unit,
                )

                self.wx_worker.configure(
                    unit=self.unit
                )

                self.wx_worker.refresh()

    def ask_city(self):
        self._menu_open = True

        try:
            city, ok = QInputDialog.getText(
                None,
                "Weather location",
                "City (leave empty to detect automatically):",
                text=self.city,
            )

        finally:
            self._menu_open = False

        if ok:
            self.city = city.strip()

            self.settings.setValue(
                "wx_city",
                self.city,
            )

            self.wx_msg = "Loading weather…"

            self.wx_worker.configure(
                city=self.city
            )

            self.wx_worker.refresh()
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
    glance.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()