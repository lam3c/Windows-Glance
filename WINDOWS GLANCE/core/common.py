"""Shared constants, helpers and theming for Windows Glance."""

import ctypes
import math
import os
import sys
import winreg

from ctypes import wintypes
from dataclasses import dataclass

from PySide6.QtCore import QDate, QLocale, QPointF, QRectF, Qt, QTime
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)


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

STATS_Y = TILES_Y + TILE_H + 10
STAT_W, STAT_H, STAT_GAP = 76, 56, 8
STAT_COUNT = 4

MEDIA_Y = STATS_Y + STAT_H + 14
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

PROGRESS_HIT = QRectF(16, MEDIA_Y + 72, EXP_W - 32, 16)

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
_set_wl.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]

VK_VOLUME = {
    "mute": 0xAD,
    "vdn": 0xAE,
    "vup": 0xAF,
}


def set_click_through(widget, on):
    """Make a window ignore the mouse (click-through) or accept it again."""

    try:
        hwnd = wintypes.HWND(int(widget.winId()))
        style = _get_wl(hwnd, GWL_EXSTYLE)

        if on:
            new_style = style | WS_EX_TRANSPARENT
        else:
            new_style = style & ~WS_EX_TRANSPARENT

        if new_style != style:
            _set_wl(hwnd, GWL_EXSTYLE, new_style)

    except Exception:
        pass


def tap_key(vk):
    """Press and release a media key."""

    _user32.keybd_event(vk, 0, 0, 0)
    _user32.keybd_event(vk, 0, 2, 0)


def left_button_down():
    return bool(_user32.GetAsyncKeyState(0x01) & 0x8000)


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
        font.setFamilies(["Segoe UI Variable Text", "Segoe UI", "Arial"])
        font.setPixelSize(px)
        font.setWeight(weight)
        _fonts[key] = font

    return _fonts[key]


def icon_font(px):
    key = ("icon", px)

    if key not in _fonts:
        font = QFont()
        font.setFamilies(["Segoe Fluent Icons", "Segoe MDL2 Assets"])
        font.setPixelSize(px)
        _fonts[key] = font

    return _fonts[key]


def draw_glyph(p, glyph, rect, px, color):
    p.setFont(icon_font(px))
    p.setPen(color)
    p.drawText(rect, Qt.AlignmentFlag.AlignCenter, glyph)


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
    lum = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
    return QColor(0, 0, 0, 230) if lum > 150 else QColor(255, 255, 255)


def with_alpha(c, a):
    return QColor(c.red(), c.green(), c.blue(), a)


def time_text():
    return QLocale.system().toString(
        QTime.currentTime(),
        QLocale.FormatType.ShortFormat,
    )


def date_text():
    return QLocale.system().toString(QDate.currentDate(), "ddd, d MMM")


def format_bytes(value):
    value = float(value)

    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024

    return f"{value:.1f} TB"


def format_rate_short(value):
    """Compact transfer rate for small stat cells, e.g. 512B, 1.5K, 3.2M."""

    value = max(0.0, float(value))
    units = ("B", "K", "M", "G")
    index = 0

    while value >= 1000 and index < len(units) - 1:
        value /= 1024
        index += 1

    if index == 0:
        return f"{value:.0f}B"

    if value < 100:
        return f"{value:.1f}{units[index]}"

    return f"{value:.0f}{units[index]}"


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

    cloud_col = QColor(228, 233, 243) if dark else QColor(128, 140, 158)
    sun_col = QColor("#FFC83D")
    moon_col = QColor(214, 222, 255) if dark else QColor(118, 130, 176)
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
                QPointF(cx + math.cos(a) * r * 1.5, cy + math.sin(a) * r * 1.5),
                QPointF(cx + math.cos(a) * r * 1.9, cy + math.sin(a) * r * 1.9),
            )

    def moon(cx, cy, r):
        a = QPainterPath()
        a.addEllipse(QPointF(cx, cy), r, r)

        b = QPainterPath()
        b.addEllipse(
            QPointF(cx + r * 0.55, cy - r * 0.35),
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
        path.addEllipse(QPointF(cx + .30 * b, cy + .62 * b), .20 * b, .20 * b)
        path.addEllipse(QPointF(cx + .50 * b, cy + .48 * b), .26 * b, .26 * b)
        path.addEllipse(QPointF(cx + .74 * b, cy + .64 * b), .18 * b, .18 * b)
        path.addRoundedRect(
            QRectF(cx + .30 * b, cy + .62 * b, .44 * b, .20 * b),
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
                QPointF(x + lx * s, y + ly1 * s),
                QPointF(x + (lx - slant) * s, y + ly2 * s),
            )

    if code in (0, 1):
        if is_day:
            sun(x + .5 * s, y + .5 * s, .2 * s)
        else:
            moon(x + .5 * s, y + .5 * s, .3 * s)

    elif code == 2:
        if is_day:
            sun(x + .36 * s, y + .36 * s, .15 * s)
        else:
            moon(x + .38 * s, y + .36 * s, .2 * s)

        cloud(.12, .22, .88)

    elif code in (45, 48):
        cloud(.0, -.04, 1.0)
        lines(cloud_col, [(.28, .82, .82), (.5, .92, .92)])

    elif code in (51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82):
        cloud(.05, -.04, .9)
        lines(
            rain_col,
            [(.34, .76, .94), (.54, .76, .94), (.74, .76, .94)],
            slant=.06,
        )

    elif 71 <= code <= 77 or code in (85, 86):
        cloud(.05, -.04, .9)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255) if dark else QColor(110, 150, 200))

        for lx in (.32, .52, .72):
            p.drawEllipse(QPointF(x + lx * s, y + .88 * s), .05 * s, .05 * s)

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
            return winreg.QueryValueEx(key, "SystemUsesLightTheme")[0] == 0

    except OSError:
        return True


def system_accent(dark):
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Accent",
        ) as key:
            value = winreg.QueryValueEx(key, "AccentColorMenu")[0]

        colour = QColor(
            value & 0xFF,
            (value >> 8) & 0xFF,
            (value >> 16) & 0xFF,
        )

        return colour.lighter(118) if dark else colour

    except OSError:
        return QColor("#60CDFF") if dark else QColor("#005FB8")


# --------------------------------------------------------------------------- #
# Run at startup
# --------------------------------------------------------------------------- #

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN_SCRIPT = os.path.join(APP_DIR, "main.py")


def startup_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, "WindowsGlance")
            return True

    except OSError:
        return False


def startup_command():
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")

    if os.path.exists(pyw):
        exe = pyw

    return f'"{exe}" "{MAIN_SCRIPT}"'


def set_startup(enabled):
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            RUN_KEY,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:

            if enabled:
                winreg.SetValueEx(
                    key,
                    "WindowsGlance",
                    0,
                    winreg.REG_SZ,
                    startup_command(),
                )

            else:
                try:
                    winreg.DeleteValue(key, "WindowsGlance")
                except OSError:
                    pass

    except OSError:
        pass