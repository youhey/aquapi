from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from aquapi.config import AppConfig, SensorConfig
from aquapi.sensors import ConfiguredSensorReading


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
