#!/usr/bin/env python3
"""Small, cache-aware weather provider used by ``key weather``.

The provider deliberately owns only structured weather data.  Map tiles and
image providers remain part of the QML runtime.  A fixture input is supported
for tests and offline development; normal operation uses Open-Meteo and a
best-effort IP location lookup.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import math
import os
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


CACHE_TTL_SECONDS = 30 * 60
USER_AGENT = "Clavis-key-weather/1"
WEATHER_CODES = {
    0: "Clear",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Rime fog",
    51: "Drizzle",
    53: "Drizzle",
    55: "Drizzle",
    56: "Freezing drizzle",
    57: "Freezing drizzle",
    61: "Rain",
    63: "Rain",
    65: "Heavy rain",
    66: "Freezing rain",
    67: "Freezing rain",
    71: "Snow",
    73: "Snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Showers",
    81: "Showers",
    82: "Heavy showers",
    85: "Snow showers",
    86: "Snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with hail",
    99: "Thunderstorm with hail",
}


def weather_text(code: Any) -> str:
    try:
        return WEATHER_CODES.get(int(code), "Unknown")
    except (TypeError, ValueError):
        return "Unknown"


def icon_name(code: Any) -> str:
    try:
        value = int(code)
    except (TypeError, ValueError):
        return "cloud"
    if value == 0:
        return "sunny"
    if value in (1, 2):
        return "partly_cloudy_day"
    if value == 3:
        return "cloud"
    if value in (45, 48):
        return "foggy"
    if 51 <= value <= 67 or 80 <= value <= 82:
        return "rainy"
    if 71 <= value <= 77 or value in (85, 86):
        return "weather_snowy"
    if value >= 95:
        return "thunderstorm"
    return "cloud"


def _xdg_dir(variable: str, fallback: Path) -> Path:
    value = os.environ.get(variable, "").strip()
    return Path(value).expanduser() if value else fallback


def cache_path(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    home = Path(os.environ.get("HOME", "~")).expanduser()
    base = _xdg_dir("CLAVIS_CACHE_HOME", _xdg_dir("XDG_CACHE_HOME", home / ".cache"))
    if os.environ.get("CLAVIS_CACHE_HOME", "") == "":
        base = base / "clavis"
    return base / "weather" / "forecast.json"


def config_path() -> Path:
    home = Path(os.environ.get("HOME", "~")).expanduser()
    base = _xdg_dir("CLAVIS_CONFIG_HOME", _xdg_dir("XDG_CONFIG_HOME", home / ".config"))
    if os.environ.get("CLAVIS_CONFIG_HOME", "") == "":
        base = base / "clavis"
    return base / "weather.json"


def _request_json(url: str, timeout: float = 8.0) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("weather endpoint returned a non-object JSON value")
    return value


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _parse_config() -> dict[str, Any]:
    value = _read_json(config_path())
    return value or {}


def _location(arguments: argparse.Namespace) -> tuple[float, float, str]:
    config = _parse_config()
    configured = config.get("location") if isinstance(config.get("location"), dict) else config
    latitude = arguments.latitude
    longitude = arguments.longitude
    name = arguments.name or str(configured.get("name", ""))
    if latitude is None:
        latitude = configured.get("latitude")
    if longitude is None:
        longitude = configured.get("longitude")
    if latitude is not None and longitude is not None:
        return float(latitude), float(longitude), name or "Manual location"

    location = _request_json("https://ipwho.is/", timeout=4.0)
    if location.get("success") is False:
        raise ValueError(str(location.get("message", "IP location lookup failed")))
    return (
        float(location["latitude"]),
        float(location["longitude"]),
        name or str(location.get("city") or location.get("region") or "Current location"),
    )


def _timezone(name: str | None) -> dt.tzinfo:
    if not name or name in {"auto", "GMT", "UTC"}:
        return dt.timezone.utc
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:
        return dt.timezone.utc


def _epoch(value: Any, timezone: dt.tzinfo) -> int | None:
    if isinstance(value, (int, float)):
        return int(value)
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone)
    return int(parsed.timestamp())


def _array_at(section: dict[str, Any], key: str, index: int, default: Any = None) -> Any:
    values = section.get(key, [])
    if not isinstance(values, list) or index >= len(values):
        return default
    return values[index]


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _current(raw: dict[str, Any], timezone: dt.tzinfo, daily: dict[str, Any]) -> dict[str, Any]:
    code = raw.get("weather_code", raw.get("weathercode", -1))
    current = {
        "time": _epoch(raw.get("time"), timezone),
        "temperatureC": _number(raw.get("temperature_2m")),
        "sourceFeelsLikeC": _number(raw.get("apparent_temperature")),
        "feelsLikeC": _number(raw.get("apparent_temperature")),
        "weatherCode": code,
        "weatherText": weather_text(code),
        "iconName": icon_name(code),
        "windSpeedMs": _number(raw.get("wind_speed_10m")),
        "windDirection": _number(raw.get("wind_direction_10m")),
        "windGustsMs": _number(raw.get("wind_gusts_10m")),
        "uvIndex": _number(raw.get("uv_index")),
        "relativeHumidity": _number(raw.get("relative_humidity_2m")),
        "dewPointC": _number(raw.get("dew_point_2m")),
        "pressureHpa": _number(raw.get("pressure_msl")),
        "cloudCover": _number(raw.get("cloud_cover")),
        "visibilityM": _number(raw.get("visibility")),
    }
    sunrise = _array_at(daily, "sunrise", 0)
    sunset = _array_at(daily, "sunset", 0)
    current["sunrise"] = _epoch(sunrise, timezone)
    current["sunset"] = _epoch(sunset, timezone)
    return current


def _hourly(raw: dict[str, Any], timezone: dt.tzinfo) -> list[dict[str, Any]]:
    times = raw.get("time", [])
    if not isinstance(times, list):
        return []
    result: list[dict[str, Any]] = []
    for index, value in enumerate(times):
        code = _array_at(raw, "weather_code", index, -1)
        result.append(
            {
                "time": _epoch(value, timezone),
                "temperatureC": _number(_array_at(raw, "temperature_2m", index)),
                "sourceFeelsLikeC": _number(_array_at(raw, "apparent_temperature", index)),
                "feelsLikeC": _number(_array_at(raw, "apparent_temperature", index)),
                "precipitationProbability": _number(_array_at(raw, "precipitation_probability", index)),
                "precipitationMm": _number(_array_at(raw, "precipitation", index)),
                "rainMm": (_number(_array_at(raw, "rain", index), 0.0) or 0.0)
                + (_number(_array_at(raw, "showers", index), 0.0) or 0.0),
                "snowCm": _number(_array_at(raw, "snowfall", index)),
                "weatherCode": code,
                "weatherText": weather_text(code),
                "iconName": icon_name(code),
                "windSpeedMs": _number(_array_at(raw, "wind_speed_10m", index)),
                "windDirection": _number(_array_at(raw, "wind_direction_10m", index)),
                "windGustsMs": _number(_array_at(raw, "wind_gusts_10m", index)),
                "uvIndex": _number(_array_at(raw, "uv_index", index)),
                "isDaylight": bool(_array_at(raw, "is_day", index, 1)),
                "relativeHumidity": _number(_array_at(raw, "relative_humidity_2m", index)),
                "dewPointC": _number(_array_at(raw, "dew_point_2m", index)),
                "pressureHpa": _number(_array_at(raw, "pressure_msl", index)),
                "cloudCover": _number(_array_at(raw, "cloud_cover", index)),
                "visibilityM": _number(_array_at(raw, "visibility", index)),
            }
        )
    now = int(time.time())
    return [item for item in result if item["time"] is None or item["time"] >= now - 3600][:48]


def _half_day(items: list[dict[str, Any]], day: bool) -> dict[str, Any]:
    selected = []
    for item in items:
        stamp = item.get("time")
        if stamp is None:
            continue
        hour = dt.datetime.fromtimestamp(stamp).hour
        if (6 <= hour < 18) == day:
            selected.append(item)
    if not selected:
        return {}
    temperatures = [x["temperatureC"] for x in selected if x.get("temperatureC") is not None]
    feels = [x["feelsLikeC"] for x in selected if x.get("feelsLikeC") is not None]
    codes = [int(x["weatherCode"]) for x in selected if str(x.get("weatherCode", "")).lstrip("-").isdigit()]
    code = max(codes, key=lambda x: (x >= 95, x >= 61 and x <= 86, x)) if codes else -1
    return {
        "temperatureC": (max(temperatures) if day else min(temperatures)) if temperatures else None,
        "feelsLikeC": (max(feels) if day else min(feels)) if feels else None,
        "precipitationMm": sum(x.get("precipitationMm") or 0 for x in selected),
        "rainMm": sum(x.get("rainMm") or 0 for x in selected),
        "snowCm": sum(x.get("snowCm") or 0 for x in selected),
        "precipitationProbability": max((x.get("precipitationProbability") or 0 for x in selected), default=0),
        "windSpeedMs": max((x.get("windSpeedMs") or 0 for x in selected), default=0),
        "windGustsMs": max((x.get("windGustsMs") or 0 for x in selected), default=0),
        "weatherCode": code,
        "weatherText": weather_text(code),
        "iconName": icon_name(code),
    }


def _daily(raw: dict[str, Any], hourly: list[dict[str, Any]], timezone: dt.tzinfo) -> list[dict[str, Any]]:
    times = raw.get("time", [])
    if not isinstance(times, list):
        return []
    result: list[dict[str, Any]] = []
    for index, date_value in enumerate(times):
        date_text = str(date_value)
        try:
            date = dt.date.fromisoformat(date_text)
        except ValueError:
            continue
        items = [x for x in hourly if x.get("time") is not None and dt.datetime.fromtimestamp(x["time"], timezone).date() == date]
        result.append(
            {
                "time": _epoch(date_text + "T00:00", timezone),
                "date": date_text,
                "temperatureMaxC": _number(_array_at(raw, "temperature_2m_max", index)),
                "temperatureMinC": _number(_array_at(raw, "temperature_2m_min", index)),
                "apparentTemperatureMaxC": _number(_array_at(raw, "apparent_temperature_max", index)),
                "apparentTemperatureMinC": _number(_array_at(raw, "apparent_temperature_min", index)),
                "sunshineDurationS": _number(_array_at(raw, "sunshine_duration", index)),
                "uvIndexMax": _number(_array_at(raw, "uv_index_max", index)),
                "relativeHumidityMean": _number(_array_at(raw, "relative_humidity_2m_mean", index)),
                "relativeHumidityMax": _number(_array_at(raw, "relative_humidity_2m_max", index)),
                "relativeHumidityMin": _number(_array_at(raw, "relative_humidity_2m_min", index)),
                "dewPointMeanC": _number(_array_at(raw, "dew_point_2m_mean", index)),
                "pressureMeanHpa": _number(_array_at(raw, "pressure_msl_mean", index)),
                "cloudCoverMean": _number(_array_at(raw, "cloud_cover_mean", index)),
                "visibilityMeanM": _number(_array_at(raw, "visibility_mean", index)),
                "sunrise": _epoch(_array_at(raw, "sunrise", index), timezone),
                "sunset": _epoch(_array_at(raw, "sunset", index), timezone),
                "moonPhaseAngle": int((date - dt.date(2000, 1, 6)).days / 29.53058867 % 1 * 360),
            }
        )
        result[-1]["day"] = _half_day(items, True)
        result[-1]["night"] = _half_day(items, False)
    return result


def _air_quality(raw: dict[str, Any], timezone: dt.tzinfo) -> dict[str, Any]:
    times = raw.get("time", [])
    if not isinstance(times, list) or not times:
        return {}
    now = int(time.time())
    index = min(range(len(times)), key=lambda i: abs((_epoch(times[i], timezone) or now) - now))
    return {
        "pm10": _number(_array_at(raw, "pm10", index)),
        "pm25": _number(_array_at(raw, "pm2_5", index)),
        "carbonMonoxide": _number(_array_at(raw, "carbon_monoxide", index)),
        "nitrogenDioxide": _number(_array_at(raw, "nitrogen_dioxide", index)),
        "sulphurDioxide": _number(_array_at(raw, "sulphur_dioxide", index)),
        "ozone": _number(_array_at(raw, "ozone", index)),
    }


def normalize(raw: dict[str, Any], latitude: float, longitude: float, name: str, air: dict[str, Any] | None = None) -> dict[str, Any]:
    timezone = _timezone(raw.get("timezone"))
    current = _current(raw.get("current", {}), timezone, raw.get("daily", {}))
    hourly = _hourly(raw.get("hourly", {}), timezone)
    daily = _daily(raw.get("daily", {}), hourly, timezone)
    current["airQuality"] = _air_quality(air or {}, timezone)
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    snapshot = {
        "valid": bool(current.get("temperatureC") is not None or hourly),
        "status": "fresh",
        "errorMessage": "",
        "locationName": name,
        "latitude": latitude,
        "longitude": longitude,
        "lastUpdated": now.isoformat().replace("+00:00", "Z"),
        "nextRefreshAt": (now + dt.timedelta(seconds=CACHE_TTL_SECONDS)).isoformat().replace("+00:00", "Z"),
        "current": current,
        "hourly": hourly,
        "daily": daily,
        "dailyTrend": daily,
        "minutely": [],
        "cacheSavedAt": time.time(),
    }
    return snapshot


def fetch(latitude: float, longitude: float, name: str, fixture: Path | None) -> dict[str, Any]:
    if fixture is not None:
        value = _read_json(fixture)
        if value is None:
            raise ValueError(f"invalid weather fixture: {fixture}")
        if "temperatureC" in (value.get("current") or {}):
            normalized = copy.deepcopy(value)
            normalized.setdefault("status", "fresh")
            normalized.setdefault("valid", True)
            normalized.setdefault("locationName", name)
            normalized.setdefault("latitude", latitude)
            normalized.setdefault("longitude", longitude)
            normalized.setdefault("cacheSavedAt", time.time())
            return normalized
        if not {"current", "hourly", "daily"}.issubset(value):
            raise ValueError(f"invalid weather fixture: {fixture}")
        return normalize(value, latitude, longitude, name)

    query = urllib.parse.urlencode(
        {
            "latitude": latitude,
            "longitude": longitude,
            "timezone": "auto",
            "past_days": 1,
            "forecast_days": 14,
            "current": ",".join(
                ["temperature_2m", "apparent_temperature", "weather_code", "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m", "uv_index", "relative_humidity_2m", "dew_point_2m", "pressure_msl", "cloud_cover", "visibility"]
            ),
            "hourly": ",".join(
                ["temperature_2m", "apparent_temperature", "precipitation_probability", "precipitation", "rain", "showers", "snowfall", "weather_code", "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m", "uv_index", "is_day", "relative_humidity_2m", "dew_point_2m", "pressure_msl", "cloud_cover", "visibility"]
            ),
            "daily": ",".join(
                ["weather_code", "temperature_2m_max", "temperature_2m_min", "apparent_temperature_max", "apparent_temperature_min", "sunshine_duration", "uv_index_max", "relative_humidity_2m_mean", "relative_humidity_2m_max", "relative_humidity_2m_min", "dew_point_2m_mean", "pressure_msl_mean", "cloud_cover_mean", "visibility_mean", "sunrise", "sunset"]
            ),
        }
    )
    forecast = _request_json(f"https://api.open-meteo.com/v1/forecast?{query}")
    air_query = urllib.parse.urlencode(
        {"latitude": latitude, "longitude": longitude, "timezone": "auto", "hourly": "pm10,pm2_5,carbon_monoxide,nitrogen_dioxide,sulphur_dioxide,ozone"}
    )
    try:
        air = _request_json(f"https://air-quality-api.open-meteo.com/v1/air-quality?{air_query}")
    except Exception:
        air = {}
    return normalize(forecast, latitude, longitude, name, air)


def stale_snapshot(value: dict[str, Any], error: str) -> dict[str, Any]:
    result = copy.deepcopy(value)
    result["status"] = "stale"
    result["errorMessage"] = error
    result["cacheSavedAt"] = result.get("cacheSavedAt", time.time())
    return result


def output_text(snapshot: dict[str, Any]) -> str:
    current = snapshot.get("current") or {}
    temperature = current.get("temperatureC")
    temp = "—" if temperature is None else f"{round(float(temperature))}°C"
    description = current.get("weatherText") or "—"
    location = snapshot.get("locationName") or "—"
    suffix = " (stale)" if snapshot.get("status") == "stale" else ""
    return f"{location}: {temp}, {description}{suffix}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch structured Clavis weather data")
    parser.add_argument("--json", action="store_true", help="emit the QML weather snapshot")
    parser.add_argument("--text", action="store_true", help="emit a human-readable summary")
    parser.add_argument("--refresh", action="store_true", help="ignore a fresh cache")
    parser.add_argument("--ttl", type=float, default=CACHE_TTL_SECONDS)
    parser.add_argument("--latitude", type=float)
    parser.add_argument("--longitude", type=float)
    parser.add_argument("--name")
    parser.add_argument("--cache")
    parser.add_argument("--fixture", type=Path)
    arguments = parser.parse_args()
    json_mode = arguments.json or not arguments.text
    path = cache_path(arguments.cache)
    cached = _read_json(path)
    now = time.time()
    cache_fresh = bool(cached and now - float(cached.get("cacheSavedAt", 0)) < arguments.ttl)
    try:
        if cache_fresh and not arguments.refresh and arguments.fixture is None:
            snapshot = copy.deepcopy(cached)
            snapshot["status"] = "cache"
        else:
            latitude, longitude, name = _location(arguments)
            snapshot = fetch(latitude, longitude, name, arguments.fixture)
            _write_json(path, snapshot)
    except Exception as error:
        if cached and cached.get("valid"):
            snapshot = stale_snapshot(cached, str(error))
        else:
            snapshot = {
                "valid": False,
                "status": "error",
                "errorMessage": str(error),
                "locationName": arguments.name or "",
                "latitude": arguments.latitude or 0.0,
                "longitude": arguments.longitude or 0.0,
                "lastUpdated": "",
                "nextRefreshAt": "",
                "current": {},
                "hourly": [],
                "daily": [],
                "dailyTrend": [],
                "minutely": [],
            }
    if json_mode:
        print(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")))
    else:
        print(output_text(snapshot))
    return 0 if snapshot.get("valid") or snapshot.get("status") in {"cache", "stale"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
