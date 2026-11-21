
"""Weather data service for Windows Glance."""

import json
import threading
import urllib.parse
import urllib.request

from PySide6.QtCore import QObject, Signal


class WeatherWorker(QObject):
    """Fetch weather from Open-Meteo using a city or automatic location."""

    updated = Signal(object)
    failed = Signal()

    def __init__(self, city="", unit="C"):
        super().__init__()
        self.city = city
        self.unit = unit
        self._loc = None
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
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "WindowsGlance/1.0"}
        )

        with urllib.request.urlopen(request, timeout=8) as response:
            return json.loads(response.read().decode("utf-8"))

    def _locate(self):
        city = self.city.strip()
        key = ("city", city) if city else ("auto",)

        if self._loc and self._loc[0] == key:
            return self._loc[1]

        if city:
            url = (
                "https://geocoding-api.open-meteo.com/v1/search"
                "?count=1&name=" + urllib.parse.quote(city)
            )

            data = self._json(url)
            result = data["results"][0]

            location = (
                result["latitude"],
                result["longitude"],
                result["name"],
            )
        else:
            try:
                data = self._json("https://ipwho.is/")

                if not data.get("success", True):
                    raise ValueError("Location lookup failed")

                location = (
                    data["latitude"],
                    data["longitude"],
                    data.get("city") or "Your location",
                )

            except Exception:
                data = self._json("https://ipapi.co/json/")

                location = (
                    data["latitude"],
                    data["longitude"],
                    data.get("city") or "Your location",
                )

        self._loc = (key, location)
        return location

    def _fetch(self):
        latitude, longitude, name = self._locate()

        unit = "fahrenheit" if self.unit == "F" else "celsius"

        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={latitude}&longitude={longitude}"
            "&current=temperature_2m,apparent_temperature,weather_code,is_day"
            "&daily=temperature_2m_max,temperature_2m_min"
            f"&timezone=auto&temperature_unit={unit}"
        )

        data = self._json(url)
        current = data["current"]
        daily = data["daily"]

        return {
            "temp": round(current["temperature_2m"]),
            "feels": round(current["apparent_temperature"]),
            "code": int(current["weather_code"]),
            "is_day": bool(current["is_day"]),
            "hi": round(daily["temperature_2m_max"][0]),
            "lo": round(daily["temperature_2m_min"][0]),
            "city": name,
        }
