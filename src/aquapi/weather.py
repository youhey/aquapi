from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import sqlite3
import sys
import time

from aquapi.config import AppConfig


@dataclass(frozen=True)
class WeatherHourlyReading:
    ts: datetime
    source: str
    latitude: float
    longitude: float
    temperature_c: float | None
    relative_humidity_percent: float | None
    wind_speed_ms: float | None
    wind_direction_deg: int | None
    precipitation_mm: float | None
    snowfall_cm: float | None
    cloud_cover_percent: int | None
    surface_pressure_hpa: float | None
    shortwave_radiation: float | None
    evapotranspiration_mm: float | None
    soil_temperature_c: float | None
    soil_moisture_m3_m3: float | None
    fetched_at: datetime | None = None


@dataclass(frozen=True)
class WeatherWriteResult:
    path: object
    saved_count: int


def fetch_weather_once(
    config: AppConfig,
    *,
    now: datetime | None = None,
    readings: list[WeatherHourlyReading] | None = None,
) -> WeatherWriteResult:
    from aquapi.open_meteo import fetch_open_meteo_hourly
    from aquapi.sqlite_storage import SQLiteStorage

    fetched_at = now or datetime.now().astimezone()
    current_readings = readings if readings is not None else fetch_open_meteo_hourly(config.weather)
    storage = SQLiteStorage(config.logging.database_path)
    storage.initialize()
    result = storage.insert_weather_hourly(current_readings, fetched_at)
    storage.apply_weather_retention(config.weather.retention_days, now=fetched_at)
    return WeatherWriteResult(path=result.path, saved_count=result.saved_count)


def collect_weather_forever(config: AppConfig) -> None:
    from aquapi.sqlite_storage import SQLiteStorage

    storage = SQLiteStorage(config.logging.database_path)
    storage.initialize()
    storage.apply_weather_retention(config.weather.retention_days)

    while True:
        try:
            fetch_weather_once(config)
        except (OSError, sqlite3.Error, RuntimeError) as exc:
            print(f"error: {exc}", file=sys.stderr)
        time.sleep(config.weather.interval_seconds)
