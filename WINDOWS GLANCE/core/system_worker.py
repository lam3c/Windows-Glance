"""Background system monitoring and media control for Windows Glance."""

import asyncio
import ctypes
import importlib
import re
import threading

from datetime import datetime, timezone
from ctypes import wintypes

from PySide6.QtCore import QObject, Signal


# ---------------------------------------------------------------------
# Windows system helpers
# ---------------------------------------------------------------------

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
    """Read battery percentage and charging state through the Windows API."""
    status = SYSTEM_POWER_STATUS()

    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
        return None

    if (status.BatteryFlag & 128) or status.BatteryLifePercent > 100:
        return None

    return {
        "percent": int(status.BatteryLifePercent),
        "plugged": status.ACLineStatus == 1,
    }


def load_winrt():
    """Load either the modern winrt packages or the older winsdk packages.-checkpoint accurate"""
    for root in ("winrt", "winsdk"):
        try:
            return {
                "control": importlib.import_module(
                    f"{root}.windows.media.control"
                ),
                "streams": importlib.import_module(
                    f"{root}.windows.storage.streams"
                ),
                "radios": importlib.import_module(
                    f"{root}.windows.devices.radios"
                ),
                "net": importlib.import_module(
                    f"{root}.windows.networking.connectivity"
                ),
            }
        except ImportError:
            continue

    return None


WINRT = load_winrt()


# ---------------------------------------------------------------------
# Main background worker sys
# ---------------------------------------------------------------------

class SystemWorker(QObject):
    """Monitor system status and control supported Windows media features."""

    status = Signal(object)
    media = Signal(object)

    def __init__(self):
        super().__init__()

        self._loop = None
        self._manager = None
        self._last_key = None

        threading.Thread(
            target=self._thread_main,
            daemon=True
        ).start()

    # -----------------------------------------------------------------
    # Background event loop 
    # -----------------------------------------------------------------

    def _thread_main(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._main())

    async def _main(self):
        if WINRT:
            try:
                control = WINRT["control"]

                self._manager = await (
                    control.GlobalSystemMediaTransportControlsSessionManager
                    .request_async()
                )
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

    # -----------------------------------------------------------------
    # Battery, Wi-Fi and Bluetooth status
    # -----------------------------------------------------------------

    async def _poll_status(self):
        wifi_on, bt_on = await self._radios()

        if WINRT:
            ssid = self._wlan_name()
        else:
            ssid = await self._netsh_ssid()

        if wifi_on is False:
            wifi_text = "Off"
            wifi_connected = False

        elif ssid:
            wifi_text = ssid
            wifi_connected = True

        else:
            wifi_text = "Not connected"
            wifi_connected = False

        self.status.emit({
            "battery": read_battery(),
            "wifi_text": wifi_text,
            "wifi_connected": wifi_connected,
            "wifi_on": wifi_on,
            "bt": bt_on,
        })

    async def _radios(self):
        wifi = None
        bluetooth = None

        if not WINRT:
            return wifi, bluetooth

        try:
            radios_module = WINRT["radios"]

            radios = await radios_module.Radio.get_radios_async()

            for radio in radios:
                if radio.kind == radios_module.RadioKind.WI_FI:
                    wifi = radio.state == radios_module.RadioState.ON

                elif radio.kind == radios_module.RadioKind.BLUETOOTH:
                    bluetooth = radio.state == radios_module.RadioState.ON

        except Exception:
            pass

        return wifi, bluetooth

    async def _set_radio(self, kind, enabled):
        radios_module = WINRT["radios"]

        wanted = (
            radios_module.RadioKind.WI_FI
            if kind == "wifi"
            else radios_module.RadioKind.BLUETOOTH
        )

        try:
            await radios_module.Radio.request_access_async()
        except Exception:
            pass

        radios = await radios_module.Radio.get_radios_async()

        for radio in radios:
            if radio.kind == wanted:
                state = (
                    radios_module.RadioState.ON
                    if enabled
                    else radios_module.RadioState.OFF
                )

                await radio.set_state_async(state)

        await self._poll_status()
        await asyncio.sleep(3.5)
        await self._poll_status()

    def _wlan_name(self):
        try:
            network = WINRT["net"].NetworkInformation
            profile = network.get_internet_connection_profile()

            if profile and profile.is_wlan_connection_profile:
                return profile.profile_name

        except Exception:
            pass

        return None

    async def _netsh_ssid(self):
        """Fallback Wi-Fi name lookup when WinRT is unavailable."""
        try:
            process = await asyncio.create_subprocess_exec(
                "netsh",
                "wlan",
                "show",
                "interfaces",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
            )

            output, _ = await asyncio.wait_for(
                process.communicate(),
                4
            )

            match = re.search(
                r"^\s*SSID\s*:\s*(\S.*)$",
                output.decode(errors="ignore"),
                re.MULTILINE,
            )

            return match.group(1).strip() if match else None

        except Exception:
            return None

    # -----------------------------------------------------------------
    # Media monitoring
    # -----------------------------------------------------------------

    async def _poll_media(self):
        if not self._manager:
            return

        control = WINRT["control"]
        session = self._manager.get_current_session()

        if session is None:
            self._last_key = None
            self.media.emit(None)
            return

        properties = await session.try_get_media_properties_async()

        title = properties.title or ""
        artist = properties.artist or ""

        if not title and not artist:
            self.media.emit(None)
            return

        playback = session.get_playback_info()
        timeline = session.get_timeline_properties()

        playing = (
            playback.playback_status
            == control.GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING
        )

        start = timeline.start_time.total_seconds()
        end = timeline.end_time.total_seconds()
        position = timeline.position.total_seconds()

        if playing:
            try:
                last_updated = timeline.last_updated_time

                now = (
                    datetime.now(timezone.utc)
                    if last_updated.tzinfo
                    else datetime.now()
                )

                position += max(
                    0.0,
                    (now - last_updated).total_seconds()
                )

            except Exception:
                pass

        key = (
            title,
            artist,
            session.source_app_user_model_id,
        )

        artwork = False

        if key != self._last_key:
            artwork = await self._read_art(properties.thumbnail)
            self._last_key = key

        self.media.emit({
            "title": title,
            "artist": artist,
            "playing": playing,
            "position": max(0.0, position - start),
            "duration": max(0.0, end - start),
            "art": artwork,
        })

    async def _read_art(self, reference):
        if reference is None:
            return None

        try:
            streams = WINRT["streams"]

            stream = await reference.open_read_async()
            size = stream.size

            if not size:
                return None

            buffer = streams.Buffer(size)

            await stream.read_async(
                buffer,
                size,
                streams.InputStreamOptions.READ_AHEAD,
            )

            reader = streams.DataReader.from_buffer(buffer)

            data = bytearray(reader.unconsumed_buffer_length)
            reader.read_bytes(data)

            return bytes(data)

        except Exception:
            return None

    # -----------------------------------------------------------------
    # Commands received from the interface
    # -----------------------------------------------------------------

    def command(self, name, arg=None):
        if not self._loop:
            return

        if name != "radio" and not self._manager:
            return

        asyncio.run_coroutine_threadsafe(
            self._run_cmd(name, arg),
            self._loop,
        )

    async def _run_cmd(self, name, arg):
        try:
            if name == "radio":
                await self._set_radio(*arg)
                return

            session = self._manager.get_current_session()

            if session is None:
                return

            if name == "toggle":
                await session.try_toggle_play_pause_async()

            elif name == "next":
                await session.try_skip_next_async()

            elif name == "prev":
                await session.try_skip_previous_async()

            elif name == "seek":
                await session.try_change_playback_position_async(
                    int(arg * 10_000_000)
                )

            await asyncio.sleep(0.2)
            await self._poll_media()

        except Exception:
            pass