"""
Windows Glance  -  a Windows 11 style status pill with weather, quick toggles and a media widget.

Install (Python 3.9 - 3.12):
    pip install PySide6 winsdk

Python 3.13+ (winsdk has no wheels there), use the modular winrt packages instead:
    pip install PySide6 winrt-runtime winrt-Windows.Foundation winrt-Windows.Foundation.Collections ^
        winrt-Windows.Media.Control winrt-Windows.Storage.Streams winrt-Windows.Devices.Radios ^
        winrt-Windows.Networking.Connectivity

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
    QDate, QEasingCurve, QLocale, QObject, QPointF, QRectF, QSettings, Qt, QTime,
    QTimer, QVariantAnimation, Signal,
)
from PySide6.QtGui import (
    QAction, QActionGroup, QBrush, QColor, QCursor, QFont, QFontMetricsF,
    QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QPolygonF,
)
from PySide6.QtWidgets import QApplication, QInputDialog, QMenu, QWidget

# --------------------------------------------------------------------------- #
#  Layout constants
# --------------------------------------------------------------------------- #
IDLE_W, IDLE_H = 46, 10    # the tiny resting sliver
PILL_H = 34                # compact (hover) height, status row
HOVER_MEDIA_H = 44         # extra height of the mini player under the status row
HOVER_MEDIA_W = 336        # minimum width while the mini player is shown
EXP_W = 360                # expanded width

TILES_Y = 76               # expanded layout (card coordinates)
TILE_W, TILE_H, TILE_GAP = 104, 64, 8
MEDIA_Y = 156
EXP_H_BASE = MEDIA_Y + 40          # nothing playing
EXP_H_MEDIA = MEDIA_Y + 124        # with media card

PAD_X, PAD_T, PAD_B = 12, 8, 16    # transparent margin for the soft shadow only
TOP_MARGIN = 8             # gap between the card and the top of the screen
SIDE_MARGIN = 16           # only used for top-left / top-right anchors

R_IDLE, R_COMPACT, R_PANEL = 4.0, 9.0, 10.0   # corner radii (soft rectangle, not a capsule)

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
#  Win32 helpers
# --------------------------------------------------------------------------- #
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020

_user32 = ctypes.windll.user32
_get_wl = getattr(_user32, "GetWindowLongPtrW", None) or _user32.GetWindowLongW
_set_wl = getattr(_user32, "SetWindowLongPtrW", None) or _user32.SetWindowLongW
_get_wl.restype = ctypes.c_ssize_t
_get_wl.argtypes = [wintypes.HWND, ctypes.c_int]
_set_wl.restype = ctypes.c_ssize_t
_set_wl.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]

VK_VOLUME = {"mute": 0xAD, "vdn": 0xAE, "vup": 0xAF}


def tap_key(vk):
    """Press and release a (media) key, exactly like the keyboard key would."""
    _user32.keybd_event(vk, 0, 0, 0)
    _user32.keybd_event(vk, 0, 2, 0)      # KEYEVENTF_KEYUP


def left_button_down():
    return bool(_user32.GetAsyncKeyState(0x01) & 0x8000)


def fullscreen_app_running():
    """True while a full-screen app / D3D game / presentation owns the screen."""
    try:
        state = ctypes.c_int()
        if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state)) == 0:
            return state.value in (2, 3, 4)
    except Exception:
        pass
    return False


# --------------------------------------------------------------------------- #
#  Helpers
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
        f = QFont()
        f.setFamilies(["Segoe UI Variable Text", "Segoe UI", "Arial"])
        f.setPixelSize(px)
        f.setWeight(weight)
        _fonts[key] = f
    return _fonts[key]


def icon_font(px):
    key = ("icon", px)
    if key not in _fonts:
        f = QFont()
        f.setFamilies(["Segoe Fluent Icons", "Segoe MDL2 Assets"])
        f.setPixelSize(px)
        _fonts[key] = f
    return _fonts[key]


def draw_glyph(p, glyph, rect, px, color):
    p.setFont(icon_font(px))
    p.setPen(color)
    p.drawText(rect, Qt.AlignmentFlag.AlignCenter, glyph)


def draw_text(p, s, x, y, font, color, max_w=None):
    if max_w is not None:
        s = QFontMetricsF(font).elidedText(s, Qt.TextElideMode.ElideRight, max(8, max_w))
    p.setFont(font)
    p.setPen(color)
    p.drawText(QPointF(x, y), s)


def contrast(c):
    lum = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
    return QColor(0, 0, 0, 230) if lum > 150 else QColor(255, 255, 255)


def with_alpha(c, a):
    return QColor(c.red(), c.green(), c.blue(), a)


def time_text():
    return QLocale.system().toString(QTime.currentTime(), QLocale.FormatType.ShortFormat)


def date_text():
    return QLocale.system().toString(QDate.currentDate(), "ddd, d MMM")


# --------------------------------------------------------------------------- #
#  Weather icons (drawn natively, no icon font needed)
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
            p.drawLine(QPointF(cx + math.cos(a) * r * 1.5, cy + math.sin(a) * r * 1.5),
                       QPointF(cx + math.cos(a) * r * 1.9, cy + math.sin(a) * r * 1.9))

    def moon(cx, cy, r):
        a = QPainterPath()
        a.addEllipse(QPointF(cx, cy), r, r)
        b = QPainterPath()
        b.addEllipse(QPointF(cx + r * 0.55, cy - r * 0.35), r * 0.85, r * 0.85)
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
        path.addRoundedRect(QRectF(cx + .30 * b, cy + .62 * b, .44 * b, .20 * b), .1 * b, .1 * b)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(cloud_col)
        p.drawPath(path.simplified())

    def lines(col, ys, slant=0.0):
        pen = QPen(col, lw)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        for lx, ly1, ly2 in ys:
            p.drawLine(QPointF(x + lx * s, y + ly1 * s), QPointF(x + (lx - slant) * s, y + ly2 * s))

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
        lines(rain_col, [(.34, .76, .94), (.54, .76, .94), (.74, .76, .94)], slant=.06)
    elif 71 <= code <= 77 or code in (85, 86):
        cloud(.05, -.04, .9)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255) if dark else QColor(110, 150, 200))
        for lx in (.32, .52, .72):
            p.drawEllipse(QPointF(x + lx * s, y + .88 * s), .05 * s, .05 * s)
    elif code >= 95:
        cloud(.05, -.1, .9)
        poly = QPolygonF([QPointF(x + a * s, y + b * s) for a, b in
                          ((.55, .6), (.38, .84), (.50, .84), (.44, 1.0), (.64, .72), (.52, .72))])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(sun_col)
        p.drawPolygon(poly)
    else:
        cloud(.0, .1, 1.0)
    p.restore()


# --------------------------------------------------------------------------- #
#  Theme / accent colour (read from Windows)
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
        ) as k:
            return winreg.QueryValueEx(k, "SystemUsesLightTheme")[0] == 0
    except OSError:
        return True


def system_accent(dark):
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Accent",
        ) as k:
            v = winreg.QueryValueEx(k, "AccentColorMenu")[0]
        c = QColor(v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF)
        return c.lighter(118) if dark else c
    except OSError:
        return QColor("#60CDFF") if dark else QColor("#005FB8")


# --------------------------------------------------------------------------- #
#  Run at startup
# --------------------------------------------------------------------------- #
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def startup_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, "WindowsGlance")
            return True
    except OSError:
        return False


def set_startup(enabled):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if enabled:
                exe = sys.executable
                pyw = exe.replace("python.exe", "pythonw.exe")
                if os.path.exists(pyw):
                    exe = pyw
                winreg.SetValueEx(k, "WindowsGlance", 0, winreg.REG_SZ,
                                  f'"{exe}" "{os.path.abspath(__file__)}"')
            else:
                try:
                    winreg.DeleteValue(k, "WindowsGlance")
                except OSError:
                    pass
    except OSError:
        pass


# --------------------------------------------------------------------------- #
#  Background workers (never touch the UI directly)
# --------------------------------------------------------------------------- #
def _load_winrt():
    for root in ("winrt", "winsdk"):
        try:
            return {
                "control": importlib.import_module(f"{root}.windows.media.control"),
                "streams": importlib.import_module(f"{root}.windows.storage.streams"),
                "radios": importlib.import_module(f"{root}.windows.devices.radios"),
                "net": importlib.import_module(f"{root}.windows.networking.connectivity"),
            }
        except ImportError:
            continue
    return None


WINRT = _load_winrt()
CREATE_NO_WINDOW = 0x08000000


class SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", ctypes.c_ubyte),
        ("BatteryFlag", ctypes.c_ubyte),
        ("BatteryLifePercent", ctypes.c_ubyte),
        ("SystemStatusFlag", ctypes.c_ubyte),
        ("BatteryLifeTime", ctypes.c_uint),
        ("BatteryFullLifeTime", ctypes.c_uint),
    ]


def read_battery():
    s = SYSTEM_POWER_STATUS()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)):
        return None
    if (s.BatteryFlag & 128) or s.BatteryLifePercent > 100:   # no battery / unknown
        return None
    return {"percent": int(s.BatteryLifePercent), "plugged": s.ACLineStatus == 1}


class WeatherWorker(QObject):
    """Open-Meteo weather (free, no API key). Location from IP or a city name."""
    updated = Signal(object)
    failed = Signal()

    def __init__(self, city="", unit="C"):
        super().__init__()
        self.city, self.unit = city, unit
        self._loc = None            # (key, (lat, lon, name))
        self._wake = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def configure(self, city=None, unit=None):
        if city is not None:
            self.city = city
        if unit is not None:
            self.unit = unit

    def refresh(self):
        self._wake.set()

    def _run(self):
        while True:
            ok = True
            try:
                self.updated.emit(self._fetch())
            except Exception:
                ok = False
                self.failed.emit()
            self._wake.wait(900 if ok else 45)
            self._wake.clear()

    @staticmethod
    def _json(url):
        req = urllib.request.Request(url, headers={"User-Agent": "WindowsGlance/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read().decode("utf-8"))

    def _locate(self):
        city = self.city.strip()
        key = ("city", city) if city else ("auto",)
        if self._loc and self._loc[0] == key:
            return self._loc[1]
        if city:
            j = self._json("https://geocoding-api.open-meteo.com/v1/search?count=1&name="
                           + urllib.parse.quote(city))
            r = j["results"][0]
            loc = (r["latitude"], r["longitude"], r["name"])
        else:
            try:
                j = self._json("https://ipwho.is/")
                if not j.get("success", True):
                    raise ValueError("ipwho failed")
                loc = (j["latitude"], j["longitude"], j.get("city") or "Your location")
            except Exception:
                j = self._json("https://ipapi.co/json/")
                loc = (j["latitude"], j["longitude"], j.get("city") or "Your location")
        self._loc = (key, loc)
        return loc

    def _fetch(self):
        lat, lon, name = self._locate()
        unit = "fahrenheit" if self.unit == "F" else "celsius"
        j = self._json(
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            "&current=temperature_2m,apparent_temperature,weather_code,is_day"
            "&daily=temperature_2m_max,temperature_2m_min"
            f"&timezone=auto&temperature_unit={unit}"
        )
        c, d = j["current"], j["daily"]
        return {
            "temp": round(c["temperature_2m"]),
            "feels": round(c["apparent_temperature"]),
            "code": int(c["weather_code"]),
            "is_day": bool(c["is_day"]),
            "hi": round(d["temperature_2m_max"][0]),
            "lo": round(d["temperature_2m_min"][0]),
            "city": name,
        }


class SystemWorker(QObject):
    status = Signal(object)
    media = Signal(object)

    def __init__(self):
        super().__init__()
        self._loop = None
        self._manager = None
        self._last_key = None
        threading.Thread(target=self._thread_main, daemon=True).start()

    # ---- thread / loop ---------------------------------------------------- #
    def _thread_main(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._main())

    async def _main(self):
        if WINRT:
            try:
                C = WINRT["control"]
                self._manager = await C.GlobalSystemMediaTransportControlsSessionManager.request_async()
            except Exception:
                self._manager = None
        tick = 0
        while True:
            try:
                if tick % 5 == 0:
                    await self._poll_status()
                await self._poll_media()
            except Exception:
                pass
            tick += 1
            await asyncio.sleep(1)

    # ---- status ----------------------------------------------------------- #
    async def _poll_status(self):
        wifi_on, bt_on = await self._radios()
        ssid = self._wlan_name() if WINRT else await self._netsh_ssid()
        if wifi_on is False:
            wifi_text, wifi_connected = "Off", False
        elif ssid:
            wifi_text, wifi_connected = ssid, True
        else:
            wifi_text, wifi_connected = "Not connected", False
        self.status.emit({
            "battery": read_battery(),
            "wifi_text": wifi_text,
            "wifi_connected": wifi_connected,
            "wifi_on": wifi_on,
            "bt": bt_on,
        })

    async def _radios(self):
        wifi = bt = None
        if not WINRT:
            return wifi, bt
        try:
            R = WINRT["radios"]
            for r in await R.Radio.get_radios_async():
                if r.kind == R.RadioKind.WI_FI:
                    wifi = r.state == R.RadioState.ON
                elif r.kind == R.RadioKind.BLUETOOTH:
                    bt = r.state == R.RadioState.ON
        except Exception:
            pass
        return wifi, bt

    async def _set_radio(self, kind, on):
        R = WINRT["radios"]
        wanted = R.RadioKind.WI_FI if kind == "wifi" else R.RadioKind.BLUETOOTH
        try:
            await R.Radio.request_access_async()
        except Exception:
            pass
        for r in await R.Radio.get_radios_async():
            if r.kind == wanted:
                await r.set_state_async(R.RadioState.ON if on else R.RadioState.OFF)
        await self._poll_status()
        await asyncio.sleep(3.5)          # give Wi-Fi time to reconnect, then refresh again
        await self._poll_status()

    def _wlan_name(self):
        try:
            prof = WINRT["net"].NetworkInformation.get_internet_connection_profile()
            if prof and prof.is_wlan_connection_profile:
                return prof.profile_name
        except Exception:
            pass
        return None

    async def _netsh_ssid(self):
        # Fallback without WinRT. "SSID" is not localised, unlike "State".
        try:
            proc = await asyncio.create_subprocess_exec(
                "netsh", "wlan", "show", "interfaces",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), 4)
            m = re.search(r"^\s*SSID\s*:\s*(\S.*)$", out.decode(errors="ignore"), re.MULTILINE)
            return m.group(1).strip() if m else None
        except Exception:
            return None

    # ---- media ------------------------------------------------------------ #
    async def _poll_media(self):
        if not self._manager:
            return
        C = WINRT["control"]
        session = self._manager.get_current_session()
        if session is None:
            self._last_key = None
            self.media.emit(None)
            return
        props = await session.try_get_media_properties_async()
        title, artist = props.title or "", props.artist or ""
        if not title and not artist:
            self.media.emit(None)
            return
        pb = session.get_playback_info()
        tl = session.get_timeline_properties()
        playing = pb.playback_status == C.GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING
        start = tl.start_time.total_seconds()
        end = tl.end_time.total_seconds()
        pos = tl.position.total_seconds()
        if playing:
            try:
                lu = tl.last_updated_time
                now = datetime.now(timezone.utc) if lu.tzinfo else datetime.now()
                pos += max(0.0, (now - lu).total_seconds())
            except Exception:
                pass
        key = (title, artist, session.source_app_user_model_id)
        art = False                                   # False = unchanged
        if key != self._last_key:
            art = await self._read_art(props.thumbnail)
            self._last_key = key
        self.media.emit({
            "title": title, "artist": artist, "playing": playing,
            "position": max(0.0, pos - start), "duration": max(0.0, end - start),
            "art": art,
        })

    async def _read_art(self, ref):
        if ref is None:
            return None
        try:
            S = WINRT["streams"]
            stream = await ref.open_read_async()
            size = stream.size
            if not size:
                return None
            buf = S.Buffer(size)
            await stream.read_async(buf, size, S.InputStreamOptions.READ_AHEAD)
            reader = S.DataReader.from_buffer(buf)
            data = bytearray(reader.unconsumed_buffer_length)
            reader.read_bytes(data)
            return bytes(data)
        except Exception:
            return None

    # ---- commands from the UI -------------------------------------------- #
    def command(self, name, arg=None):
        if not self._loop:
            return
        if name != "radio" and not self._manager:
            return
        asyncio.run_coroutine_threadsafe(self._run_cmd(name, arg), self._loop)

    async def _run_cmd(self, name, arg):
        try:
            if name == "radio":
                await self._set_radio(*arg)
                return
            s = self._manager.get_current_session()
            if s is None:
                return
            if name == "toggle":
                await s.try_toggle_play_pause_async()
            elif name == "next":
                await s.try_skip_next_async()
            elif name == "prev":
                await s.try_skip_previous_async()
            elif name == "seek":
                await s.try_change_playback_position_async(int(arg * 10_000_000))
            await asyncio.sleep(0.2)
            await self._poll_media()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
#  The widget
# --------------------------------------------------------------------------- #
class WindowsGlance(QWidget):
    ANCHORS = (("top-center", "Top center"), ("top-right", "Top right"), ("top-left", "Top left"))

    def __init__(self):
        super().__init__()
        self.settings = QSettings("WindowsGlance", "Glance")
        self.anchor = self.settings.value("anchor", "top-center")
        if self.anchor not in dict(self.ANCHORS):
            self.anchor = "top-center"
        self.unit = self.settings.value("wx_unit", "C")
        if self.unit not in ("C", "F"):
            self.unit = "C"
        self.city = str(self.settings.value("wx_city", "") or "")

        # state
        self.state = "idle"                 # idle | hover | expanded
        self.hover_item = None
        self._left_at = None
        self._enter_at = None
        self._menu_open = False
        self._radio_lock_until = 0.0
        self.battery = None
        self.wifi_text, self.wifi_connected, self.wifi_on = "Checking…", False, None
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

        # window: frameless, always on top, never steals focus
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMouseTracking(True)

        # The window is resized to hug the card on every frame, so nothing
        # outside the card is ever covered. _w/_h = animated card size.
        self._w, self._h = float(IDLE_W), float(IDLE_H)
        self._end = (self._w, self._h)
        self._card = QRectF()               # card in window coordinates
        self._gcard = QRectF()              # card in screen coordinates
        self.anim = QVariantAnimation(self)
        self.anim.valueChanged.connect(self._on_anim)
        self.apply_geometry()

        scr = QApplication.primaryScreen()
        scr.geometryChanged.connect(self.apply_geometry)
        scr.availableGeometryChanged.connect(self.apply_geometry)

        # timers (cheap; the heavy work is in the worker threads)
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.poll_cursor)
        self.poll_timer.start(30)
        self.tick_timer = QTimer(self)      # equalizer + progress bar, only while playing
        self.tick_timer.timeout.connect(self.update)
        self.clock_timer = QTimer(self)     # clock refresh while visible
        self.clock_timer.timeout.connect(self._on_clock)
        self.clock_timer.start(1000)
        self.fs_timer = QTimer(self)        # hide during full-screen apps
        self.fs_timer.timeout.connect(self.check_fullscreen)
        self.fs_timer.start(1500)

        self.worker = SystemWorker()
        self.worker.status.connect(self.on_status)
        self.worker.media.connect(self.on_media)
        self.wx_worker = WeatherWorker(self.city, self.unit)
        self.wx_worker.updated.connect(self.on_weather)
        self.wx_worker.failed.connect(self.on_weather_failed)

    # ------------------------------------------------------------------ #
    #  Win32: click-through while idle
    # ------------------------------------------------------------------ #
    def _set_click_through(self, on):
        try:
            hwnd = wintypes.HWND(int(self.winId()))
            style = _get_wl(hwnd, GWL_EXSTYLE)
            new = (style | WS_EX_TRANSPARENT) if on else (style & ~WS_EX_TRANSPARENT)
            if new != style:
                _set_wl(hwnd, GWL_EXSTYLE, new)
        except Exception:
            pass

    def showEvent(self, e):
        super().showEvent(e)
        self._set_click_through(self.state == "idle")

    def check_fullscreen(self):
        busy = fullscreen_app_running()
        if busy and self.isVisible():
            self.hide()
        elif not busy and not self.isVisible():
            self.show()

    def _on_clock(self):
        if self.state != "idle" and self.isVisible():
            self.retarget(160)          # width follows the clock text
            self.update()

    # ------------------------------------------------------------------ #
    #  Geometry / animation
    # ------------------------------------------------------------------ #
    def apply_geometry(self, *_):
        g = QApplication.primaryScreen().geometry()
        w, h = self._w, self._h
        if self.anchor == "top-right":
            sx = g.right() + 1 - SIDE_MARGIN - w
        elif self.anchor == "top-left":
            sx = g.left() + SIDE_MARGIN
        else:
            sx = g.left() + (g.width() - w) / 2
        top = g.top() + TOP_MARGIN
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
        return PILL_H + (HOVER_MEDIA_H if self.media else 0)

    def exp_height(self):
        return EXP_H_MEDIA if self.media else EXP_H_BASE

    def compact_width(self):
        return 32 + self._status_row(None, 0, 0, 0)

    def target_size(self):
        if self.state == "expanded":
            return (float(EXP_W), float(self.exp_height()))
        if self.state == "hover":
            w = self.compact_width()
            if self.media:
                w = max(w, HOVER_MEDIA_W)
            return (float(w), float(self.hover_height()))
        return (float(IDLE_W), float(IDLE_H))

    def retarget(self, duration=None):
        end = self.target_size()
        if end == self._end:
            return
        self._end = end
        self.anim.stop()
        if duration is None:
            duration = 380 if self.state == "expanded" else 260
        self.anim.setDuration(duration)
        self.anim.setEasingCurve(
            QEasingCurve.Type.OutQuart if self.state == "expanded" else QEasingCurve.Type.OutCubic
        )
        self.anim.setStartValue(QRectF(0, 0, self._w, self._h))
        self.anim.setEndValue(QRectF(0, 0, end[0], end[1]))
        self.anim.start()

    def _on_anim(self, value):
        self._w, self._h = value.width(), value.height()
        self.apply_geometry()

    def set_state(self, s):
        if s == self.state:
            return
        self.state = s
        self._left_at = self._enter_at = None
        if s == "idle":
            self.hover_item = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
        self._set_click_through(s == "idle")
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
    #  Data from the workers
    # ------------------------------------------------------------------ #
    def on_status(self, s):
        self.battery = s["battery"]
        if time.perf_counter() >= self._radio_lock_until:      # don't fight an in-flight toggle
            self.wifi_text, self.wifi_connected = s["wifi_text"], s["wifi_connected"]
            self.wifi_on, self.bt = s["wifi_on"], s["bt"]
        dark = system_uses_dark()
        self.dark, self.th, self.accent = dark, make_theme(dark), system_accent(dark)
        self.retarget(200)
        self.update()

    def on_weather(self, w):
        self.weather = w
        self.retarget(200)
        self.update()

    def on_weather_failed(self):
        if self.weather is None:
            self.wx_msg = "Weather unavailable"
            self.update()

    def on_media(self, info):
        if info is None:
            self.media, self.art = None, None
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
        self.art = None
        if data:
            pm = QPixmap()
            if pm.loadFromData(data) and not pm.isNull():
                self.art = pm
                side = min(pm.width(), pm.height())
                self._art_src = QRectF((pm.width() - side) / 2, (pm.height() - side) / 2, side, side)

    def cur_pos(self):
        m = self.media
        pos = m["position"]
        if m["playing"]:
            pos += time.perf_counter() - self.media_t
        return min(pos, m["duration"]) if m["duration"] else pos

    # ------------------------------------------------------------------ #
    #  Actions
    # ------------------------------------------------------------------ #
    def toggle_radio(self, kind):
        if not WINRT:                                   # no WinRT: fall back to Settings
            os.startfile(SETTINGS_URI[kind])
            return
        if kind == "wifi":
            cur = self.wifi_on if self.wifi_on is not None else self.wifi_connected
            self.wifi_on = not cur
            self.wifi_connected = False if not self.wifi_on else self.wifi_connected
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
    #  Mouse
    # ------------------------------------------------------------------ #
    def poll_cursor(self):
        if self._menu_open or not self.isVisible():
            return
        cur = QPointF(QCursor.pos())
        gc = self._gcard
        now = time.perf_counter()

        if self.state == "idle":
            zone = gc.adjusted(-40, -TOP_MARGIN - 4, 40, 12)     # invisible trigger zone
            if zone.contains(cur) and not left_button_down():    # not while dragging a window
                if self._enter_at is None:
                    self._enter_at = now
                elif now - self._enter_at > 0.12:                # ignore quick fly-bys
                    self.set_state("hover")
            else:
                self._enter_at = None
            return

        zone = gc.adjusted(-6, -TOP_MARGIN - 4, 6, 8)
        if zone.contains(cur):
            self._left_at = None
        else:
            if self.hover_item is not None:
                self.hover_item = None
                self.update()
            if self._left_at is None:
                self._left_at = now
            elif now - self._left_at > (1.0 if self.state == "expanded" else 0.3):
                self.set_state("idle")

    def hover_ctrls(self, w):
        """Mini-player buttons in the hover pill (card coordinates, card width w)."""
        y = PILL_H + 7
        return {
            "prev": QRectF(w - 104, y, 30, 30),
            "play": QRectF(w - 74, y, 30, 30),
            "next": QRectF(w - 44, y, 30, 30),
        }

    def vol_rects(self):
        y = MEDIA_Y + 38 if self.media else MEDIA_Y - 6
        return {"mute": QRectF(248, y, 32, 32), "vdn": QRectF(280, y, 32, 32), "vup": QRectF(312, y, 32, 32)}

    def hit(self, rel):
        if self.state == "hover":
            if self.media:
                for name, r in self.hover_ctrls(self._card.width()).items():
                    if r.contains(rel):
                        return ("btn", name)
            return None
        if self.state != "expanded":
            return None
        for i in range(3):
            if QRectF(16 + i * (TILE_W + TILE_GAP), TILES_Y, TILE_W, TILE_H).contains(rel):
                return ("tile", i)
        for name, r in self.vol_rects().items():
            if r.contains(rel):
                return ("vol", name)
        if self.media:
            for name, r in CTRL.items():
                if r.contains(rel):
                    return ("btn", name)
            if self.media["duration"] and PROGRESS_HIT.contains(rel):
                return ("seek", None)
        return None

    def mouseMoveEvent(self, e):
        item = self.hit(e.position() - self._card.topLeft())
        if item != self.hover_item:
            self.hover_item = item
            self.setCursor(Qt.CursorShape.PointingHandCursor if item and item != ("tile", 2)
                           else Qt.CursorShape.ArrowCursor)
            self.update()

    def mousePressEvent(self, e):
        pos = e.position()
        if not self._card.contains(pos):
            return
        rel = pos - self._card.topLeft()
        item = self.hit(rel)

        if e.button() == Qt.MouseButton.RightButton:
            if item and item[0] == "tile" and item[1] in (0, 1):     # right-click = open Settings
                os.startfile(SETTINGS_URI[("wifi", "bt")[item[1]]])
            else:
                self.show_menu(e.globalPosition().toPoint())
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return

        if self.state == "hover":
            if item and item[0] == "btn":
                self.media_action(item[1])
            else:
                self.set_state("expanded")
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
            frac = clamp((rel.x() - 16) / (EXP_W - 32))
            target = frac * self.media["duration"]
            self.media["position"], self.media_t = target, time.perf_counter()
            self.worker.command("seek", target)
            self.update()

    # ------------------------------------------------------------------ #
    #  Context menu (Windows 11 flavoured)
    # ------------------------------------------------------------------ #
    def _style_menu(self, m):
        th = self.th
        bg = f"rgb({th.bg.red()},{th.bg.green()},{th.bg.blue()})"
        txt = f"rgb({th.text.red()},{th.text.green()},{th.text.blue()})"
        hov = "rgba(255,255,255,0.09)" if th.dark else "rgba(0,0,0,0.06)"
        brd = "rgba(255,255,255,0.10)" if th.dark else "rgba(0,0,0,0.12)"
        m.setWindowFlags(m.windowFlags() | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.NoDropShadowWindowHint)
        m.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        m.setStyleSheet(f"""
            QMenu {{ background:{bg}; color:{txt}; border:1px solid {brd};
                     border-radius:8px; padding:4px; font-family:'Segoe UI Variable Text','Segoe UI'; font-size:13px; }}
            QMenu::item {{ padding:7px 28px 7px 12px; border-radius:4px; margin:2px 2px; }}
            QMenu::item:selected {{ background:{hov}; }}
            QMenu::separator {{ height:1px; background:{brd}; margin:4px 8px; }}
        """)

    def show_menu(self, gpos):
        menu = QMenu(self)
        self._style_menu(menu)

        pos_menu = menu.addMenu("Position")
        self._style_menu(pos_menu)
        group = QActionGroup(pos_menu)
        for key, label in self.ANCHORS:
            a = QAction(label, pos_menu, checkable=True)
            a.setChecked(key == self.anchor)
            a.setData(key)
            group.addAction(a)
            pos_menu.addAction(a)

        unit_menu = menu.addMenu("Weather units")
        self._style_menu(unit_menu)
        ugroup = QActionGroup(unit_menu)
        for u, label in (("C", "Celsius (°C)"), ("F", "Fahrenheit (°F)")):
            a = QAction(label, unit_menu, checkable=True)
            a.setChecked(self.unit == u)
            a.setData("unit:" + u)
            ugroup.addAction(a)
            unit_menu.addAction(a)
        city_a = menu.addAction("Weather location…")

        menu.addSeparator()
        startup = QAction("Run at startup", menu, checkable=True)
        startup.setChecked(startup_enabled())
        menu.addAction(startup)
        menu.addSeparator()
        quit_a = menu.addAction("Exit")

        self._menu_open = True
        try:
            chosen = menu.exec(gpos)
        finally:
            self._menu_open = False
        if chosen is None:
            return
        if chosen is quit_a:
            QApplication.quit()
        elif chosen is startup:
            set_startup(startup.isChecked())
        elif chosen is city_a:
            self.ask_city()
        else:
            d = chosen.data()
            if isinstance(d, str) and d in dict(self.ANCHORS):
                self.anchor = d
                self.settings.setValue("anchor", d)
                self.apply_geometry()
            elif isinstance(d, str) and d.startswith("unit:"):
                self.unit = d[5:]
                self.settings.setValue("wx_unit", self.unit)
                self.wx_worker.configure(unit=self.unit)
                self.wx_worker.refresh()

    def ask_city(self):
        self._menu_open = True
        try:
            text, ok = QInputDialog.getText(
                None, "Weather location",
                "City (leave empty to detect automatically):", text=self.city)
        finally:
            self._menu_open = False
        if ok:
            self.city = text.strip()
            self.settings.setValue("wx_city", self.city)
            self.wx_msg = "Loading weather…"
            self.wx_worker.configure(city=self.city)
            self.wx_worker.refresh()
            self.update()

    # ------------------------------------------------------------------ #
    #  Painting
    # ------------------------------------------------------------------ #
    def _draw_shadow(self, p, card, r):
        p.setPen(Qt.PenStyle.NoPen)
        steps = 9
        for i in range(steps - 1, -1, -1):
            grow = i * 1.2
            a = int(self.th.shadow * (1 - i / steps) ** 1.6)
            if a <= 0:
                continue
            p.setBrush(QColor(0, 0, 0, a))
            rr = card.adjusted(-grow, -grow + 2, grow, grow + 4)
            p.drawRoundedRect(rr, r + grow, r + grow)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.TextAntialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
        )
        th, card = self.th, self._card
        hh, eh = self.hover_height(), self.exp_height()
        show = clamp((card.height() - IDLE_H) / (PILL_H - IDLE_H))        # idle -> compact
        open_ = clamp((card.height() - hh) / (eh - hh))                    # hover -> expanded
        wprog = clamp((card.width() - 100) / (EXP_W - 100))
        r = min(card.height() / 2,
                R_IDLE + (R_COMPACT - R_IDLE) * show + (R_PANEL - R_COMPACT) * open_)
        base = 0.45 + 0.55 * show           # idle = see-through

        p.setOpacity(show)
        self._draw_shadow(p, card, r)

        p.setOpacity(base)
        path = QPainterPath()
        path.addRoundedRect(card, r, r)
        p.fillPath(path, th.bg)
        sheen = QLinearGradient(card.topLeft(), card.bottomLeft())
        sheen.setColorAt(0.0, th.sheen)
        sheen.setColorAt(0.6, QColor(255, 255, 255, 0))
        p.fillPath(path, QBrush(sheen))
        if self.state == "hover":
            p.fillPath(path, th.hover_overlay)

        p.setClipPath(path)
        ia = clamp(1 - show * 2.2)
        if ia > 0:
            p.setOpacity(ia * 0.9)
            self._paint_idle(p, card)
        ca = clamp((show - 0.55) / 0.45) * clamp(1 - open_ * 3.2)
        if ca > 0:
            p.setOpacity(ca)
            self._paint_compact(p, card)
        ea = clamp((min(open_, wprog) - 0.55) / 0.45)
        if ea > 0:
            p.setOpacity(ea)
            p.save()
            p.translate(card.topLeft())
            self._paint_expanded(p)
            p.restore()
        p.setClipping(False)

        # Fluent-style border: lighter on top, softer at the bottom
        p.setOpacity(base)
        grad = QLinearGradient(card.topLeft(), card.bottomLeft())
        grad.setColorAt(0.0, th.border_hi)
        grad.setColorAt(1.0, th.border_lo)
        p.setPen(QPen(QBrush(grad), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        outline = QPainterPath()
        outline.addRoundedRect(card.adjusted(0.5, 0.5, -0.5, -0.5), r - 0.5, r - 0.5)
        p.drawPath(outline)
        p.end()

    # ---- battery drawn natively (horizontal, Windows 11 style) ---------- #
    def _draw_battery(self, p, rect, level, plugged):
        th = self.th
        col = th.good if plugged else (th.bad if level <= 20 else th.text)
        body = QRectF(rect.left(), rect.top(), rect.width() - 2.5, rect.height())
        p.setPen(QPen(with_alpha(col, 210), 1.1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(body.adjusted(0.55, 0.55, -0.55, -0.55), 2.5, 2.5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(QRectF(body.right() + 0.8, rect.center().y() - 2, 1.6, 4), 0.8, 0.8)
        inner = body.adjusted(2, 2, -2, -2)
        p.drawRoundedRect(
            QRectF(inner.left(), inner.top(), max(1.5, inner.width() * level / 100), inner.height()),
            1.2, 1.2,
        )
        if plugged:
            b = body
            pts = [(0.56, 0.12), (0.32, 0.56), (0.50, 0.56), (0.44, 0.90), (0.70, 0.42), (0.52, 0.42)]
            poly = QPolygonF([QPointF(b.left() + x * b.width(), b.top() + y * b.height()) for x, y in pts])
            p.setBrush(QColor(th.bg.red(), th.bg.green(), th.bg.blue()))
            p.drawPolygon(poly)

    def _draw_eq(self, p, x, cy, bar_h=10.0, min_h=4.0, step=4.5, w=2.5):
        t = time.perf_counter()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self.accent)
        for i in range(4):
            h = min_h + bar_h * (0.5 + 0.5 * math.sin(t * 6.5 + i * 1.7))
            p.drawRoundedRect(QRectF(x + i * step, cy - h / 2, w, h), w / 2, w / 2)

    # ---- idle sliver ------------------------------------------------------ #
    def _paint_idle(self, p, card):
        if self.is_playing():
            self._draw_eq(p, card.center().x() - 8.5, card.center().y(), 3.5, 2.0, 5.0, 2.5)

    # ---- hover pill: clock, weather, Wi-Fi, Bluetooth, battery ------------ #
    def _status_row(self, p, x0, cy, base):
        """Draws the status row (p given) or just measures it (p is None). Returns its width."""
        th = self.th
        f_t, f = ui_font(12, QFont.Weight.DemiBold), ui_font(12)
        fmb, fm = QFontMetricsF(f_t), QFontMetricsF(f)
        draw = p is not None
        x = x0

        tt = time_text()
        if draw:
            draw_text(p, tt, x, base, f_t, th.text)
        x += fmb.horizontalAdvance(tt)

        if self.weather:
            x += 10
            if draw:
                p.fillRect(QRectF(x, cy - 7, 1, 14), with_alpha(th.text, 45))
            x += 11
            if draw:
                draw_weather(p, self.weather["code"], self.weather["is_day"], QRectF(x, cy - 8, 16, 16), th.dark)
            x += 22
            t = f'{self.weather["temp"]}°'
            if draw:
                draw_text(p, t, x, base, f, th.text)
            x += fm.horizontalAdvance(t)

        x += 10
        if draw:
            p.fillRect(QRectF(x, cy - 7, 1, 14), with_alpha(th.text, 45))
        x += 11

        wifi_active = self.wifi_on if self.wifi_on is not None else self.wifi_connected
        if draw:
            draw_glyph(p, "\uE701", QRectF(x, cy - 9, 14, 18), 14, th.text if wifi_active else th.sub)
        x += 14 + 10
        if draw:
            draw_glyph(p, "\uE702", QRectF(x, cy - 9, 14, 18), 14, th.text if self.bt else th.sub)
        x += 14
        if self.battery:
            x += 12
            if draw:
                self._draw_battery(p, QRectF(x, cy - 5.5, 22, 11), self.battery["percent"], self.battery["plugged"])
            x += 22 + 5
            pct = f'{self.battery["percent"]}%'
            if draw:
                draw_text(p, pct, x, base, f, th.text)
            x += fm.horizontalAdvance("100%")
        return x - x0

    def _paint_compact(self, p, card):
        fm = QFontMetricsF(ui_font(12))
        cy = card.top() + min(card.height(), PILL_H) / 2
        base = cy + (fm.ascent() - fm.descent()) / 2
        row_w = self._status_row(None, 0, 0, 0)
        self._status_row(p, card.center().x() - row_w / 2, cy, base)
        if self.media:
            self._paint_hover_media(p, card)

    def _paint_hover_media(self, p, card):
        th, m = self.th, self.media
        top = card.top() + PILL_H
        p.fillRect(QRectF(card.left() + 12, top - 1, card.width() - 24, 1), with_alpha(th.text, 22))

        art = QRectF(card.left() + 12, top + 6, 32, 32)
        path = QPainterPath()
        path.addRoundedRect(art, 5, 5)
        if self.art:
            p.save()
            p.setClipPath(path, Qt.ClipOperation.IntersectClip)
            p.drawPixmap(art, self.art, self._art_src)
            p.restore()
        else:
            p.fillPath(path, th.tile_hover)
            draw_glyph(p, "\uE8D6", art, 15, th.sub)

        avail = card.width() - 54 - 112
        draw_text(p, m["title"], card.left() + 54, top + 19, ui_font(12, QFont.Weight.DemiBold), th.text, avail)
        draw_text(p, m["artist"] or "Unknown artist", card.left() + 54, top + 34, ui_font(11), th.sub, avail)

        glyphs = {"prev": ("\uE892", 13), "play": ("\uE769" if m["playing"] else "\uE768", 15), "next": ("\uE893", 13)}
        for name, rel in self.hover_ctrls(card.width()).items():
            rect = rel.translated(card.topLeft())
            if self.hover_item == ("btn", name):
                bp = QPainterPath()
                bp.addRoundedRect(rect, 6, 6)
                p.fillPath(bp, th.tile_hover)
            g, px = glyphs[name]
            draw_glyph(p, g, rect, px, th.text)

        if m["duration"]:                                   # hairline progress
            frac = clamp(self.cur_pos() / m["duration"])
            x0, wd = card.left() + 12, card.width() - 24
            y = card.top() + PILL_H + HOVER_MEDIA_H - 4
            p.fillRect(QRectF(x0, y, wd, 2), with_alpha(th.text, 36))
            p.fillRect(QRectF(x0, y, max(2.0, wd * frac), 2), self.accent)

    # ---- expanded panel (coordinates relative to the card) ---------------- #
    def _paint_weather_strip(self, p):
        th, w = self.th, self.weather
        tf, df = ui_font(24, QFont.Weight.DemiBold), ui_font(11)
        tt, ds = time_text(), date_text()
        draw_text(p, tt, EXP_W - 16 - QFontMetricsF(tf).horizontalAdvance(tt), 38, tf, th.text)
        draw_text(p, ds, EXP_W - 16 - QFontMetricsF(df).horizontalAdvance(ds), 56, df, th.sub)
        if w:
            draw_weather(p, w["code"], w["is_day"], QRectF(16, 14, 40, 40), th.dark)
            big = ui_font(26, QFont.Weight.DemiBold)
            t = f'{w["temp"]}°'
            draw_text(p, t, 66, 38, big, th.text)
            bw = QFontMetricsF(big).horizontalAdvance(t)
            draw_text(p, f'H {w["hi"]}°  L {w["lo"]}°', 66 + bw + 8, 37, df, th.sub)
            draw_text(p, f'{wx_text(w["code"])} · {w["city"]}', 66, 56, ui_font(12), th.sub, 200)
        else:
            draw_text(p, self.wx_msg, 16, 42, ui_font(12), th.sub)

    def _draw_tile(self, p, idx, label, status, active, glyph):
        th = self.th
        rect = QRectF(16 + idx * (TILE_W + TILE_GAP), TILES_Y, TILE_W, TILE_H)
        hov = self.hover_item == ("tile", idx) and idx != 2
        if active:
            fill = self.accent.lighter(110) if hov else self.accent
            fg = contrast(self.accent)
            sub = with_alpha(fg, 205)
        else:
            fill = th.tile_hover if hov else th.tile
            fg, sub = th.text, th.sub
        path = QPainterPath()
        path.addRoundedRect(rect, 6, 6)
        p.fillPath(path, fill)
        if not active:
            p.setPen(QPen(th.stroke, 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        if glyph:
            draw_glyph(p, glyph, QRectF(rect.left() + 12, rect.top() + 8, 20, 20), 16, fg)
        elif self.battery:
            self._draw_battery(p, QRectF(rect.left() + 12, rect.top() + 12, 26, 13),
                               self.battery["percent"], self.battery["plugged"])
        else:
            draw_glyph(p, "\uE83E", QRectF(rect.left() + 12, rect.top() + 8, 20, 20), 16, fg)
        draw_text(p, label, rect.left() + 12, rect.top() + 42, ui_font(12, QFont.Weight.DemiBold), fg, TILE_W - 20)
        draw_text(p, status, rect.left() + 12, rect.top() + 56, ui_font(11), sub, TILE_W - 20)

    def _paint_expanded(self, p):
        th = self.th
        self._paint_weather_strip(p)

        bt_text = {True: "On", False: "Off", None: "Unavailable"}[self.bt]
        if self.battery:
            b = self.battery
            batt_text = f'{b["percent"]}% · {"Plugged in" if b["plugged"] else "On battery"}'
        else:
            batt_text = "No battery"
        wifi_active = self.wifi_on if self.wifi_on is not None else self.wifi_connected
        self._draw_tile(p, 0, "Wi-Fi", self.wifi_text, wifi_active, "\uE701")
        self._draw_tile(p, 1, "Bluetooth", bt_text, self.bt is True, "\uE702")
        self._draw_tile(p, 2, "Battery", batt_text, False, None)

        if self.media:
            self._paint_media(p)
        else:
            draw_glyph(p, "\uE8D6", QRectF(16, MEDIA_Y - 2, 18, 18), 14, th.sub)
            draw_text(p, "Nothing is playing", 40, MEDIA_Y + 11, ui_font(12), th.sub)
        self._paint_volume(p)

    def _paint_volume(self, p):
        th = self.th
        for name, r in self.vol_rects().items():
            if self.hover_item == ("vol", name):
                bp = QPainterPath()
                bp.addRoundedRect(r, 6, 6)
                p.fillPath(bp, th.tile_hover)
            if name == "mute":
                draw_glyph(p, "\uE74F", r, 15, th.text)
            else:
                p.setFont(ui_font(19))
                p.setPen(th.text)
                p.drawText(r, Qt.AlignmentFlag.AlignCenter, "\u2212" if name == "vdn" else "+")

    def _paint_media(self, p):
        th, m, y0 = self.th, self.media, MEDIA_Y
        art = QRectF(16, y0, 64, 64)
        path = QPainterPath()
        path.addRoundedRect(art, 6, 6)
        if self.art:
            p.save()
            p.setClipPath(path, Qt.ClipOperation.IntersectClip)
            p.drawPixmap(art, self.art, self._art_src)
            p.restore()
            p.setPen(QPen(th.stroke, 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        else:
            p.fillPath(path, th.tile_hover)
            draw_glyph(p, "\uE8D6", art, 24, th.sub)

        draw_text(p, m["title"], 92, y0 + 16, ui_font(14, QFont.Weight.DemiBold), th.text, EXP_W - 108)
        draw_text(p, m["artist"] or "Unknown artist", 92, y0 + 33, ui_font(12), th.sub, EXP_W - 108)

        glyphs = {"prev": ("\uE892", 14), "play": ("\uE769" if m["playing"] else "\uE768", 17), "next": ("\uE893", 14)}
        for name, rect in CTRL.items():
            if self.hover_item == ("btn", name):
                bp = QPainterPath()
                bp.addRoundedRect(rect, 6, 6)
                p.fillPath(bp, th.tile_hover)
            g, px = glyphs[name]
            draw_glyph(p, g, rect, px, th.text)

        if m["duration"]:
            pos = self.cur_pos()
            frac = clamp(pos / m["duration"])
            track = QRectF(16, y0 + 80, EXP_W - 32, 4)
            tp = QPainterPath()
            tp.addRoundedRect(track, 2, 2)
            p.fillPath(tp, with_alpha(th.text, 40))
            if frac > 0:
                fp = QPainterPath()
                fp.addRoundedRect(QRectF(track.left(), track.top(), max(4, track.width() * frac), 4), 2, 2)
                p.fillPath(fp, self.accent)
            if self.hover_item == ("seek", None):          # scrub handle on hover
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(self.accent)
                p.drawEllipse(QPointF(track.left() + track.width() * frac, track.center().y()), 5, 5)
            f = ui_font(11)
            draw_text(p, fmt_time(pos), 16, y0 + 102, f, th.sub)
            end = fmt_time(m["duration"])
            draw_text(p, end, EXP_W - 16 - QFontMetricsF(f).horizontalAdvance(end), y0 + 102, f, th.sub)


# --------------------------------------------------------------------------- #
def main():
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)        # dialogs / hiding must never quit the app
    glance = WindowsGlance()
    glance.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()