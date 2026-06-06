from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from aquapi.config import AppConfig, SensorConfig
from aquapi.sensors import ConfiguredSensorReading
from aquapi.weather import WeatherHourlyReading


SCHEMA_VERSION = "1"


@dataclass(frozen=True)
class SQLiteWriteResult:
    path: Path
    saved_count: int


@dataclass(frozen=True)
class DatabaseStats:
    path: Path
    readings_count: int
    sensors_count: int
    first_ts: int | None
    last_ts: int | None


class SQLiteStorage:
    def __init__(self, database_path: Path):
        self.database_path = database_path

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            with conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS sensors (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      device_id TEXT NOT NULL UNIQUE,
                      name TEXT NOT NULL,
                      type TEXT NOT NULL,
                      offset_milli_c INTEGER NOT NULL DEFAULT 0,
                      min_milli_c INTEGER,
                      max_milli_c INTEGER,
                      created_at INTEGER NOT NULL,
                      updated_at INTEGER NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS readings (
                      sensor_id INTEGER NOT NULL,
                      ts INTEGER NOT NULL,
                      raw_temperature_milli_c INTEGER,
                      temperature_milli_c INTEGER,
                      status TEXT NOT NULL,
                      crc_ok INTEGER NOT NULL,
                      error TEXT,
                      PRIMARY KEY (sensor_id, ts),
                      FOREIGN KEY (sensor_id) REFERENCES sensors(id)
                    );

                    CREATE INDEX IF NOT EXISTS idx_readings_ts
                    ON readings(ts);

                    CREATE INDEX IF NOT EXISTS idx_readings_sensor_ts
                    ON readings(sensor_id, ts);

                    CREATE TABLE IF NOT EXISTS metadata (
                      key TEXT PRIMARY KEY,
                      value TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS weather_hourly (
                      ts INTEGER PRIMARY KEY,
                      source TEXT NOT NULL,
                      latitude_microdeg INTEGER NOT NULL,
                      longitude_microdeg INTEGER NOT NULL,

                      temperature_milli_c INTEGER,
                      relative_humidity_milli_percent INTEGER,
                      wind_speed_milli_ms INTEGER,
                      wind_direction_deg INTEGER,
                      precipitation_milli_mm INTEGER,
                      snowfall_milli_cm INTEGER,
                      cloud_cover_percent INTEGER,
                      surface_pressure_milli_hpa INTEGER,
                      shortwave_radiation INTEGER,
                      evapotranspiration_milli_mm INTEGER,
                      soil_temperature_milli_c INTEGER,
                      soil_moisture_milli_m3_m3 INTEGER,

                      fetched_at INTEGER NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_weather_hourly_ts
                    ON weather_hourly(ts);
                    """
                )
                conn.execute(
                    """
                    INSERT INTO metadata (key, value)
                    VALUES ('schema_version', ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (SCHEMA_VERSION,),
                )

    def sync_sensors(self, config: AppConfig) -> None:
        self.initialize()
        now = int(time.time())
        with closing(self._connect()) as conn:
            with conn:
                conn.executemany(
                    """
                    INSERT INTO sensors (
                      device_id,
                      name,
                      type,
                      offset_milli_c,
                      min_milli_c,
                      max_milli_c,
                      created_at,
                      updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(device_id) DO UPDATE SET
                      name = excluded.name,
                      type = excluded.type,
                      offset_milli_c = excluded.offset_milli_c,
                      min_milli_c = excluded.min_milli_c,
                      max_milli_c = excluded.max_milli_c,
                      updated_at = excluded.updated_at
                    """,
                    [_sensor_config_row(sensor_config, now) for sensor_config in config.sensors.values()],
                )

    def insert_readings(
        self,
        readings: list[ConfiguredSensorReading],
        ts: datetime,
    ) -> SQLiteWriteResult:
        self.initialize()
        timestamp = int(ts.timestamp())
        with closing(self._connect()) as conn:
            with conn:
                sensor_ids = {
                    reading.sensor_id: _ensure_sensor(conn, reading, timestamp)
                    for reading in readings
                }
                conn.executemany(
                    """
                    INSERT INTO readings (
                      sensor_id,
                      ts,
                      raw_temperature_milli_c,
                      temperature_milli_c,
                      status,
                      crc_ok,
                      error
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(sensor_id, ts) DO UPDATE SET
                      raw_temperature_milli_c = excluded.raw_temperature_milli_c,
                      temperature_milli_c = excluded.temperature_milli_c,
                      status = excluded.status,
                      crc_ok = excluded.crc_ok,
                      error = excluded.error
                    """,
                    [
                        (
                            sensor_ids[reading.sensor_id],
                            timestamp,
                            _float_c_to_milli(reading.raw_temperature_c),
                            _float_c_to_milli(reading.temperature_c),
                            reading.status,
                            1 if reading.crc_ok else 0,
                            reading.error,
                        )
                        for reading in readings
                    ],
                )

        return SQLiteWriteResult(path=self.database_path, saved_count=len(readings))

    def get_series(
        self,
        *,
        range_text: str,
        start: datetime,
        end: datetime,
        sensor_id: str | None = None,
        name: str | None = None,
    ) -> dict[str, object] | None:
        self.initialize()
        where = ["r.ts >= ?", "r.ts <= ?"]
        params: list[object] = [int(start.timestamp()), int(end.timestamp())]
        if sensor_id is not None:
            where.append("s.device_id = ?")
            params.append(sensor_id)
        if name is not None:
            where.append("s.name = ?")
            params.append(name)

        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"""
                SELECT
                  s.device_id,
                  s.name,
                  r.ts,
                  r.temperature_milli_c,
                  r.raw_temperature_milli_c,
                  r.status,
                  r.crc_ok
                FROM readings r
                JOIN sensors s ON s.id = r.sensor_id
                WHERE {" AND ".join(where)}
                ORDER BY r.ts ASC
                """,
                params,
            ).fetchall()

        if not rows:
            return None

        return {
            "sensor_id": rows[0]["device_id"],
            "name": rows[0]["name"],
            "range": range_text,
            "points": [
                {
                    "ts": _ts_to_iso(row["ts"]),
                    "temperature_c": _milli_to_float_c(row["temperature_milli_c"]),
                    "raw_temperature_c": _milli_to_float_c(row["raw_temperature_milli_c"]),
                    "status": row["status"],
                    "crc_ok": bool(row["crc_ok"]),
                }
                for row in rows
            ],
        }

    def get_summary(self, *, range_text: str, start: datetime, end: datetime) -> dict[str, object]:
        self.initialize()
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT
                  s.id,
                  s.device_id,
                  s.name,
                  COUNT(r.temperature_milli_c) AS sample_count,
                  MIN(r.temperature_milli_c) AS min_temperature_milli_c,
                  AVG(r.temperature_milli_c) AS avg_temperature_milli_c,
                  MAX(r.temperature_milli_c) AS max_temperature_milli_c
                FROM sensors s
                JOIN readings r ON r.sensor_id = s.id
                WHERE r.ts >= ? AND r.ts <= ? AND r.temperature_milli_c IS NOT NULL
                GROUP BY s.id, s.device_id, s.name
                ORDER BY s.device_id ASC
                """,
                (int(start.timestamp()), int(end.timestamp())),
            ).fetchall()
            latest_by_sensor = {
                row["sensor_id"]: row
                for row in conn.execute(
                    """
                    SELECT sensor_id, temperature_milli_c, status
                    FROM readings r1
                    WHERE ts = (
                      SELECT MAX(ts)
                      FROM readings r2
                      WHERE r2.sensor_id = r1.sensor_id
                        AND r2.ts >= ?
                        AND r2.ts <= ?
                    )
                    """,
                    (int(start.timestamp()), int(end.timestamp())),
                ).fetchall()
            }

        return {
            "range": range_text,
            "sensors": [
                {
                    "sensor_id": row["device_id"],
                    "name": row["name"],
                    "sample_count": row["sample_count"],
                    "min_temperature_c": _milli_to_float_c(row["min_temperature_milli_c"]),
                    "avg_temperature_c": _milli_to_float_c(row["avg_temperature_milli_c"]),
                    "max_temperature_c": _milli_to_float_c(row["max_temperature_milli_c"]),
                    "latest_temperature_c": _milli_to_float_c(
                        latest_by_sensor[row["id"]]["temperature_milli_c"]
                    )
                    if row["id"] in latest_by_sensor
                    else None,
                    "latest_status": latest_by_sensor[row["id"]]["status"]
                    if row["id"] in latest_by_sensor
                    else None,
                }
                for row in rows
            ],
        }

    def apply_retention(self, retention_days: int, *, now: datetime | None = None) -> None:
        self.initialize()
        if retention_days <= 0:
            return
        current = now or datetime.now().astimezone()
        cutoff = int((current - timedelta(days=retention_days)).timestamp())
        with closing(self._connect()) as conn:
            with conn:
                conn.execute("DELETE FROM readings WHERE ts < ?", (cutoff,))

    def stats(self) -> DatabaseStats:
        self.initialize()
        with closing(self._connect()) as conn:
            readings = conn.execute(
                "SELECT COUNT(*) AS count, MIN(ts) AS first_ts, MAX(ts) AS last_ts FROM readings"
            ).fetchone()
            sensors = conn.execute("SELECT COUNT(*) AS count FROM sensors").fetchone()

        return DatabaseStats(
            path=self.database_path,
            readings_count=readings["count"],
            sensors_count=sensors["count"],
            first_ts=readings["first_ts"],
            last_ts=readings["last_ts"],
        )

    def insert_weather_hourly(
        self,
        readings: list[WeatherHourlyReading],
        fetched_at: datetime,
    ) -> SQLiteWriteResult:
        self.initialize()
        fetched_at_ts = int(fetched_at.timestamp())
        with closing(self._connect()) as conn:
            with conn:
                conn.executemany(
                    """
                    INSERT INTO weather_hourly (
                      ts,
                      source,
                      latitude_microdeg,
                      longitude_microdeg,
                      temperature_milli_c,
                      relative_humidity_milli_percent,
                      wind_speed_milli_ms,
                      wind_direction_deg,
                      precipitation_milli_mm,
                      snowfall_milli_cm,
                      cloud_cover_percent,
                      surface_pressure_milli_hpa,
                      shortwave_radiation,
                      evapotranspiration_milli_mm,
                      soil_temperature_milli_c,
                      soil_moisture_milli_m3_m3,
                      fetched_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(ts) DO UPDATE SET
                      source = excluded.source,
                      latitude_microdeg = excluded.latitude_microdeg,
                      longitude_microdeg = excluded.longitude_microdeg,
                      temperature_milli_c = excluded.temperature_milli_c,
                      relative_humidity_milli_percent = excluded.relative_humidity_milli_percent,
                      wind_speed_milli_ms = excluded.wind_speed_milli_ms,
                      wind_direction_deg = excluded.wind_direction_deg,
                      precipitation_milli_mm = excluded.precipitation_milli_mm,
                      snowfall_milli_cm = excluded.snowfall_milli_cm,
                      cloud_cover_percent = excluded.cloud_cover_percent,
                      surface_pressure_milli_hpa = excluded.surface_pressure_milli_hpa,
                      shortwave_radiation = excluded.shortwave_radiation,
                      evapotranspiration_milli_mm = excluded.evapotranspiration_milli_mm,
                      soil_temperature_milli_c = excluded.soil_temperature_milli_c,
                      soil_moisture_milli_m3_m3 = excluded.soil_moisture_milli_m3_m3,
                      fetched_at = excluded.fetched_at
                    """,
                    [_weather_row(reading, fetched_at_ts) for reading in readings],
                )

        return SQLiteWriteResult(path=self.database_path, saved_count=len(readings))

    def get_latest_weather(self, *, now: datetime | None = None) -> dict[str, object] | None:
        self.initialize()
        current = int((now or datetime.now().astimezone()).timestamp())
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT *
                FROM weather_hourly
                WHERE ts <= ?
                ORDER BY ts DESC
                LIMIT 1
                """,
                (current,),
            ).fetchone()
        if row is None:
            return None
        return _weather_row_to_dict(row)

    def get_weather_series(self, *, start: datetime, end: datetime) -> list[dict[str, object]]:
        self.initialize()
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM weather_hourly
                WHERE ts >= ? AND ts <= ?
                ORDER BY ts ASC
                """,
                (int(start.timestamp()), int(end.timestamp())),
            ).fetchall()
        return [_weather_row_to_dict(row) for row in rows]

    def get_weather_summary(self, *, range_text: str, start: datetime, end: datetime) -> dict[str, object]:
        self.initialize()
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT
                  COUNT(*) AS sample_count,
                  MIN(temperature_milli_c) AS min_temperature_milli_c,
                  AVG(temperature_milli_c) AS avg_temperature_milli_c,
                  MAX(temperature_milli_c) AS max_temperature_milli_c,
                  MIN(relative_humidity_milli_percent) AS min_relative_humidity_milli_percent,
                  AVG(relative_humidity_milli_percent) AS avg_relative_humidity_milli_percent,
                  MAX(relative_humidity_milli_percent) AS max_relative_humidity_milli_percent,
                  SUM(precipitation_milli_mm) AS total_precipitation_milli_mm
                FROM weather_hourly
                WHERE ts >= ? AND ts <= ?
                """,
                (int(start.timestamp()), int(end.timestamp())),
            ).fetchone()

        return {
            "range": range_text,
            "sample_count": row["sample_count"],
            "temperature": {
                "min_c": _milli_to_float_c(row["min_temperature_milli_c"]),
                "avg_c": _milli_to_float_c(row["avg_temperature_milli_c"]),
                "max_c": _milli_to_float_c(row["max_temperature_milli_c"]),
            },
            "relative_humidity": {
                "min_percent": _milli_to_float_c(row["min_relative_humidity_milli_percent"]),
                "avg_percent": _milli_to_float_c(row["avg_relative_humidity_milli_percent"]),
                "max_percent": _milli_to_float_c(row["max_relative_humidity_milli_percent"]),
            },
            "precipitation": {
                "total_mm": _milli_to_float_c(row["total_precipitation_milli_mm"]),
            },
        }

    def apply_weather_retention(self, retention_days: int, *, now: datetime | None = None) -> None:
        self.initialize()
        if retention_days <= 0:
            return
        current = now or datetime.now().astimezone()
        cutoff = int((current - timedelta(days=retention_days)).timestamp())
        with closing(self._connect()) as conn:
            with conn:
                conn.execute("DELETE FROM weather_hourly WHERE ts < ?", (cutoff,))

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn


def _sensor_config_row(sensor_config: SensorConfig, now: int) -> tuple[object, ...]:
    return (
        sensor_config.sensor_id,
        sensor_config.name,
        sensor_config.type,
        _float_c_to_milli(sensor_config.offset) or 0,
        _float_c_to_milli(sensor_config.min),
        _float_c_to_milli(sensor_config.max),
        now,
        now,
    )


def _ensure_sensor(conn: sqlite3.Connection, reading: ConfiguredSensorReading, now: int) -> int:
    conn.execute(
        """
        INSERT INTO sensors (
          device_id,
          name,
          type,
          offset_milli_c,
          min_milli_c,
          max_milli_c,
          created_at,
          updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(device_id) DO UPDATE SET
          name = excluded.name,
          type = excluded.type,
          offset_milli_c = excluded.offset_milli_c,
          min_milli_c = excluded.min_milli_c,
          max_milli_c = excluded.max_milli_c,
          updated_at = excluded.updated_at
        """,
        (
            reading.sensor_id,
            reading.name,
            reading.type,
            _float_c_to_milli(reading.offset) or 0,
            _float_c_to_milli(reading.min),
            _float_c_to_milli(reading.max),
            now,
            now,
        ),
    )
    row = conn.execute(
        "SELECT id FROM sensors WHERE device_id = ?",
        (reading.sensor_id,),
    ).fetchone()
    return int(row["id"])


def _float_c_to_milli(value: float | None) -> int | None:
    if value is None:
        return None
    return int(round(value * 1000))


def _milli_to_float_c(value: int | float | None) -> float | None:
    if value is None:
        return None
    return float(value) / 1000


def _ts_to_iso(ts: int) -> str:
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def _weather_row(reading: WeatherHourlyReading, fetched_at_ts: int) -> tuple[object, ...]:
    return (
        int(reading.ts.timestamp()),
        reading.source,
        _float_to_scaled_int(reading.latitude, 1_000_000),
        _float_to_scaled_int(reading.longitude, 1_000_000),
        _float_to_scaled_int(reading.temperature_c, 1000),
        _float_to_scaled_int(reading.relative_humidity_percent, 1000),
        _float_to_scaled_int(reading.wind_speed_ms, 1000),
        reading.wind_direction_deg,
        _float_to_scaled_int(reading.precipitation_mm, 1000),
        _float_to_scaled_int(reading.snowfall_cm, 1000),
        reading.cloud_cover_percent,
        _float_to_scaled_int(reading.surface_pressure_hpa, 1000),
        _float_to_scaled_int(reading.shortwave_radiation, 1),
        _float_to_scaled_int(reading.evapotranspiration_mm, 1000),
        _float_to_scaled_int(reading.soil_temperature_c, 1000),
        _float_to_scaled_int(reading.soil_moisture_m3_m3, 1000),
        fetched_at_ts,
    )


def _weather_row_to_dict(row: sqlite3.Row) -> dict[str, object]:
    return {
        "ts": _ts_to_iso(row["ts"]),
        "source": row["source"],
        "latitude": _scaled_int_to_float(row["latitude_microdeg"], 1_000_000),
        "longitude": _scaled_int_to_float(row["longitude_microdeg"], 1_000_000),
        "temperature_c": _milli_to_float_c(row["temperature_milli_c"]),
        "relative_humidity_percent": _milli_to_float_c(row["relative_humidity_milli_percent"]),
        "wind_speed_ms": _milli_to_float_c(row["wind_speed_milli_ms"]),
        "wind_direction_deg": row["wind_direction_deg"],
        "precipitation_mm": _milli_to_float_c(row["precipitation_milli_mm"]),
        "snowfall_cm": _milli_to_float_c(row["snowfall_milli_cm"]),
        "cloud_cover_percent": row["cloud_cover_percent"],
        "surface_pressure_hpa": _milli_to_float_c(row["surface_pressure_milli_hpa"]),
        "shortwave_radiation": row["shortwave_radiation"],
        "evapotranspiration_mm": _milli_to_float_c(row["evapotranspiration_milli_mm"]),
        "soil_temperature_c": _milli_to_float_c(row["soil_temperature_milli_c"]),
        "soil_moisture_m3_m3": _milli_to_float_c(row["soil_moisture_milli_m3_m3"]),
        "fetched_at": _ts_to_iso(row["fetched_at"]),
    }


def _float_to_scaled_int(value: float | None, scale: int) -> int | None:
    if value is None:
        return None
    return int(round(value * scale))


def _scaled_int_to_float(value: int | None, scale: int) -> float | None:
    if value is None:
        return None
    return value / scale
