from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aquapi.config import WeatherConfig
from aquapi.weather import WeatherHourlyReading


OPEN_METEO_ENDPOINT = "https://api.open-meteo.com/v1/forecast"
HOURLY_VARIABLES = [
    "temperature_2m",
    "relative_humidity_2m",
    "wind_speed_10m",
    "wind_direction_10m",
    "precipitation",
    "snowfall",
    "cloud_cover",
    "surface_pressure",
    "shortwave_radiation",
    "et0_fao_evapotranspiration",
    "soil_temperature_0cm",
    "soil_moisture_0_to_1cm",
]


class OpenMeteoError(RuntimeError):
    pass


def fetch_open_meteo_hourly(
    config: WeatherConfig,
    *,
    timeout_seconds: float = 10.0,
) -> list[WeatherHourlyReading]:
    url = build_open_meteo_url(config)
    request = Request(url, headers={"User-Agent": "aquapi/0.1"})
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            body = response.read()
    except OSError as exc:
        raise OpenMeteoError(f"Open-Meteo request failed: {exc}") from exc

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OpenMeteoError("Open-Meteo response is not valid JSON") from exc

    return parse_open_meteo_hourly(payload, config)


def build_open_meteo_url(config: WeatherConfig) -> str:
    query = urlencode(
        {
            "latitude": config.latitude,
            "longitude": config.longitude,
            "hourly": ",".join(HOURLY_VARIABLES),
            "timezone": config.timezone,
            "forecast_days": config.forecast_days,
            "wind_speed_unit": "ms",
        }
    )
    return f"{OPEN_METEO_ENDPOINT}?{query}"


def parse_open_meteo_hourly(
    payload: dict[str, Any],
    config: WeatherConfig,
) -> list[WeatherHourlyReading]:
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict):
        raise OpenMeteoError("Open-Meteo response missing hourly object")

    times = hourly.get("time")
    if not isinstance(times, list):
        raise OpenMeteoError("Open-Meteo response missing hourly.time array")

    timezone = _configured_timezone(config)
    readings: list[WeatherHourlyReading] = []
    for index, raw_time in enumerate(times):
        if not isinstance(raw_time, str):
            raise OpenMeteoError("Open-Meteo hourly.time contains non-string value")

        try:
            ts = datetime.fromisoformat(raw_time)
        except ValueError as exc:
            raise OpenMeteoError(f"invalid Open-Meteo hourly time: {raw_time}") from exc
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone)

        readings.append(
            WeatherHourlyReading(
                ts=ts,
                source=config.source,
                latitude=config.latitude,
                longitude=config.longitude,
                temperature_c=_optional_float(hourly, "temperature_2m", index),
                relative_humidity_percent=_optional_float(hourly, "relative_humidity_2m", index),
                wind_speed_ms=_optional_float(hourly, "wind_speed_10m", index),
                wind_direction_deg=_optional_int(hourly, "wind_direction_10m", index),
                precipitation_mm=_optional_float(hourly, "precipitation", index),
                snowfall_cm=_optional_float(hourly, "snowfall", index),
                cloud_cover_percent=_optional_int(hourly, "cloud_cover", index),
                surface_pressure_hpa=_optional_float(hourly, "surface_pressure", index),
                shortwave_radiation=_optional_float(hourly, "shortwave_radiation", index),
                evapotranspiration_mm=_optional_float(hourly, "et0_fao_evapotranspiration", index),
                soil_temperature_c=_optional_float(hourly, "soil_temperature_0cm", index),
                soil_moisture_m3_m3=_optional_float(hourly, "soil_moisture_0_to_1cm", index),
            )
        )

    return readings


def _optional_float(hourly: dict[str, Any], key: str, index: int) -> float | None:
    values = hourly.get(key)
    if not isinstance(values, list) or index >= len(values):
        return None
    value = values[index]
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _optional_int(hourly: dict[str, Any], key: str, index: int) -> int | None:
    value = _optional_float(hourly, key, index)
    if value is None:
        return None
    return int(round(value))


def _configured_timezone(config: WeatherConfig) -> ZoneInfo:
    try:
        return ZoneInfo(config.timezone)
    except ZoneInfoNotFoundError as exc:
        raise OpenMeteoError(f"invalid weather timezone: {config.timezone}") from exc
