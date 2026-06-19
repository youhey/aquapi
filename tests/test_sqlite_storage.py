import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from aquapi.config import AppConfig, EnvironmentSensorConfig, LoggingConfig, SensorConfig
from aquapi.environment import EnvironmentReading
from aquapi.sqlite_storage import SQLiteStorage
from aquapi.sensors import ConfiguredSensorReading
from aquapi.weather import WeatherHourlyReading


class SQLiteStorageTests(unittest.TestCase):
    def test_initialize_creates_schema_and_metadata(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "aquapi.sqlite3"
            storage = SQLiteStorage(db_path)

            storage.initialize()

            with sqlite3.connect(db_path) as conn:
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }
                schema_version = conn.execute(
                    "SELECT value FROM metadata WHERE key = 'schema_version'"
                ).fetchone()[0]
                sensor_columns = {
                    row[1] for row in conn.execute("PRAGMA table_info(sensors)").fetchall()
                }

        self.assertIn("sensors", tables)
        self.assertIn("readings", tables)
        self.assertIn("metadata", tables)
        self.assertIn("weather_hourly", tables)
        self.assertIn("role", sensor_columns)
        self.assertIn("enabled", sensor_columns)
        self.assertIn("visible", sensor_columns)
        self.assertIn("sort_order", sensor_columns)
        self.assertIn("short_name", sensor_columns)
        self.assertIn("short_name_ascii", sensor_columns)
        self.assertIn("display_code", sensor_columns)
        self.assertIn("environment_readings", tables)
        self.assertEqual(schema_version, "5")

    def test_sync_sensors_inserts_and_updates_sensor_config(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            storage = SQLiteStorage(Path(tmp_dir) / "aquapi.sqlite3")
            storage.initialize()
            storage.sync_sensors(make_config(name="増田川水槽", offset=-0.2))
            storage.sync_sensors(
                make_config(
                    name="更新後",
                    offset=0.1,
                    role="outdoor",
                    enabled=False,
                    visible=False,
                    sort_order=20,
                    short_name="更新",
                    short_name_ascii="UPDATED",
                    display_code="UPD",
                )
            )

            with sqlite3.connect(storage.database_path) as conn:
                row = conn.execute(
                    """
                    SELECT name, short_name, short_name_ascii, display_code, type, role, enabled, visible, sort_order,
                           offset_milli_c, min_milli_c, max_milli_c
                    FROM sensors
                    WHERE device_id = '28-00000020f5ed'
                    """
                ).fetchone()

        self.assertEqual(
            row,
            ("更新後", "更新", "UPDATED", "UPD", "water", "outdoor", 0, 0, 20, 100, 18000, 28000),
        )

    def test_initialize_migrates_existing_sensors_table(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "aquapi.sqlite3"
            with sqlite3.connect(db_path) as conn:
                conn.executescript(
                    """
                    CREATE TABLE sensors (
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
                    """
                )

            SQLiteStorage(db_path).initialize()

            with sqlite3.connect(db_path) as conn:
                sensor_columns = {
                    row[1] for row in conn.execute("PRAGMA table_info(sensors)").fetchall()
                }
                schema_version = conn.execute(
                    "SELECT value FROM metadata WHERE key = 'schema_version'"
                ).fetchone()[0]

        self.assertIn("role", sensor_columns)
        self.assertIn("enabled", sensor_columns)
        self.assertIn("visible", sensor_columns)
        self.assertIn("sort_order", sensor_columns)
        self.assertIn("short_name", sensor_columns)
        self.assertIn("short_name_ascii", sensor_columns)
        self.assertIn("display_code", sensor_columns)
        self.assertEqual(schema_version, "5")

    def test_insert_readings_saves_multiple_sensors_and_handles_duplicates(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            storage = SQLiteStorage(Path(tmp_dir) / "aquapi.sqlite3")
            storage.initialize()
            ts = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
            readings = [
                make_reading("28-1", temperature_c=23.187),
                make_reading("28-2", temperature_c=24.0),
                make_reading("28-3", temperature_c=25.0),
                make_reading("28-4", temperature_c=26.0),
                make_reading("28-5", temperature_c=None, status="error", crc_ok=False),
            ]

            first = storage.insert_readings(readings, ts)
            second = storage.insert_readings(readings, ts)

            with sqlite3.connect(storage.database_path) as conn:
                count = conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
                null_count = conn.execute(
                    "SELECT COUNT(*) FROM readings WHERE temperature_milli_c IS NULL"
                ).fetchone()[0]

        self.assertEqual(first.saved_count, 5)
        self.assertEqual(second.saved_count, 5)
        self.assertEqual(count, 5)
        self.assertEqual(null_count, 1)

    def test_get_series_by_sensor_id_and_name_honors_range(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            storage = SQLiteStorage(Path(tmp_dir) / "aquapi.sqlite3")
            now = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
            storage.insert_readings([make_reading()], now - timedelta(hours=2))
            storage.insert_readings([make_reading(temperature_c=24.0)], now - timedelta(minutes=30))

            by_id = storage.get_series(
                sensor_id="28-00000020f5ed",
                range_text="1h",
                start=now - timedelta(hours=1),
                end=now,
            )
            by_name = storage.get_series(
                name="増田川水槽",
                range_text="1h",
                start=now - timedelta(hours=1),
                end=now,
            )
            missing = storage.get_series(
                sensor_id="28-missing",
                range_text="1h",
                start=now - timedelta(hours=1),
                end=now,
            )

        assert by_id is not None
        assert by_name is not None
        self.assertEqual(len(by_id["points"]), 1)
        self.assertEqual(by_id["points"][0]["temperature_c"], 24.0)
        self.assertEqual(by_name["sensor_id"], "28-00000020f5ed")
        self.assertIsNone(missing)

    def test_get_summary_returns_aggregates_per_sensor(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            storage = SQLiteStorage(Path(tmp_dir) / "aquapi.sqlite3")
            now = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
            storage.insert_readings(
                [
                    make_reading("28-1", name="A", temperature_c=22.0, status="low"),
                    make_reading("28-2", name="B", temperature_c=25.0, status="ok"),
                ],
                now - timedelta(minutes=2),
            )
            storage.insert_readings(
                [
                    make_reading("28-1", name="A", temperature_c=24.0, status="ok"),
                    make_reading("28-2", name="B", temperature_c=27.0, status="high"),
                ],
                now - timedelta(minutes=1),
            )

            payload = storage.get_summary(
                range_text="1h",
                start=now - timedelta(hours=1),
                end=now,
            )

        self.assertEqual(len(payload["sensors"]), 2)
        first = payload["sensors"][0]
        self.assertEqual(first["sensor_id"], "28-1")
        self.assertEqual(first["sample_count"], 2)
        self.assertEqual(first["min_temperature_c"], 22.0)
        self.assertEqual(first["avg_temperature_c"], 23.0)
        self.assertEqual(first["max_temperature_c"], 24.0)
        self.assertEqual(first["latest_temperature_c"], 24.0)
        self.assertEqual(first["latest_status"], "ok")

    def test_apply_retention_deletes_old_readings_but_keeps_sensors(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            storage = SQLiteStorage(Path(tmp_dir) / "aquapi.sqlite3")
            now = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
            storage.insert_readings([make_reading()], now - timedelta(days=3))
            storage.insert_readings([make_reading(temperature_c=24.0)], now)

            storage.apply_retention(1, now=now)

            with sqlite3.connect(storage.database_path) as conn:
                readings_count = conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
                sensors_count = conn.execute("SELECT COUNT(*) FROM sensors").fetchone()[0]

        self.assertEqual(readings_count, 1)
        self.assertEqual(sensors_count, 1)

    def test_insert_weather_hourly_saves_and_updates_same_ts(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            storage = SQLiteStorage(Path(tmp_dir) / "aquapi.sqlite3")
            ts = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
            fetched_at = datetime(2026, 6, 6, 11, 58, tzinfo=timezone.utc)

            first = storage.insert_weather_hourly([make_weather(ts, temperature_c=25.1)], fetched_at)
            second = storage.insert_weather_hourly([make_weather(ts, temperature_c=26.2)], fetched_at)
            latest = storage.get_latest_weather(now=ts)

            with sqlite3.connect(storage.database_path) as conn:
                count = conn.execute("SELECT COUNT(*) FROM weather_hourly").fetchone()[0]

        self.assertEqual(first.saved_count, 1)
        self.assertEqual(second.saved_count, 1)
        self.assertEqual(count, 1)
        assert latest is not None
        self.assertEqual(latest["temperature_c"], 26.2)
        self.assertEqual(latest["latitude"], 35.681236)

    def test_get_latest_weather_ignores_future_forecast(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            storage = SQLiteStorage(Path(tmp_dir) / "aquapi.sqlite3")
            now = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)

            storage.insert_weather_hourly(
                [
                    make_weather(now - timedelta(hours=1), temperature_c=25.1),
                    make_weather(now + timedelta(hours=12), temperature_c=30.0),
                ],
                now,
            )
            latest = storage.get_latest_weather(now=now)

        assert latest is not None
        self.assertEqual(latest["temperature_c"], 25.1)

    def test_get_weather_series_and_retention(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            storage = SQLiteStorage(Path(tmp_dir) / "aquapi.sqlite3")
            now = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
            storage.insert_weather_hourly(
                [
                    make_weather(now - timedelta(days=3), temperature_c=20.0),
                    make_weather(now - timedelta(hours=1), temperature_c=25.1),
                ],
                now,
            )

            storage.apply_weather_retention(1, now=now)
            series = storage.get_weather_series(start=now - timedelta(days=7), end=now)

        self.assertEqual(len(series), 1)
        self.assertEqual(series[0]["temperature_c"], 25.1)

    def test_get_weather_summary_returns_min_avg_max_and_precipitation(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            storage = SQLiteStorage(Path(tmp_dir) / "aquapi.sqlite3")
            now = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
            storage.insert_weather_hourly(
                [
                    make_weather(now - timedelta(hours=2), temperature_c=20.0, precipitation_mm=1.2),
                    make_weather(now - timedelta(hours=1), temperature_c=26.0, precipitation_mm=0.8),
                ],
                now,
            )

            summary = storage.get_weather_summary(
                range_text="24h",
                start=now - timedelta(hours=24),
                end=now,
            )

        self.assertEqual(summary["sample_count"], 2)
        self.assertEqual(summary["temperature"]["min_c"], 20.0)
        self.assertEqual(summary["temperature"]["avg_c"], 23.0)
        self.assertEqual(summary["temperature"]["max_c"], 26.0)
        self.assertEqual(summary["precipitation"]["total_mm"], 2.0)

    def test_insert_environment_readings_and_get_latest_series_summary(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            storage = SQLiteStorage(Path(tmp_dir) / "aquapi.sqlite3")
            config = make_environment_config()
            now = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
            storage.insert_environment_readings(
                [make_environment_reading(temperature_c=24.0, humidity_percent=55.0)],
                now - timedelta(minutes=2),
            )
            storage.insert_environment_readings(
                [make_environment_reading(temperature_c=26.0, humidity_percent=57.0)],
                now - timedelta(minutes=1),
            )

            latest = storage.get_environment_latest(config)
            series = storage.get_environment_series(
                config,
                range_text="24h",
                start=now - timedelta(hours=24),
                end=now,
                sensor_key="sht31_room",
            )
            summary = storage.get_environment_summary(
                config,
                range_text="24h",
                start=now - timedelta(hours=24),
                end=now,
            )

        sensor = latest["sensors"][0]
        self.assertEqual(sensor["sensor_key"], "sht31_room")
        self.assertEqual(sensor["temperature_c"], 26.0)
        self.assertEqual(sensor["relative_humidity_percent"], 57.0)
        self.assertEqual(len(series["points"]), 2)
        self.assertEqual(series["points"][0]["temperature_c"], 24.0)
        self.assertEqual(summary["sensors"][0]["samples"], 2)
        self.assertEqual(summary["sensors"][0]["temperature"]["min_c"], 24.0)
        self.assertEqual(summary["sensors"][0]["temperature"]["avg_c"], 25.0)
        self.assertEqual(summary["sensors"][0]["temperature"]["max_c"], 26.0)
        self.assertEqual(summary["sensors"][0]["relative_humidity"]["current_percent"], 57.0)

    def test_environment_latest_returns_definition_without_data_and_honors_visibility(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            storage = SQLiteStorage(Path(tmp_dir) / "aquapi.sqlite3")
            config = make_environment_config(visible=False)

            hidden = storage.get_environment_latest(config)
            included = storage.get_environment_latest(config, include_hidden=True)

        self.assertEqual(hidden["sensors"], [])
        self.assertEqual(included["sensors"][0]["sensor_key"], "sht31_room")
        self.assertIsNone(included["sensors"][0]["temperature_c"])
        self.assertIsNone(included["sensors"][0]["relative_humidity_percent"])


def make_config(
    *,
    name: str,
    offset: float,
    role: str = "aquarium",
    enabled: bool = True,
    visible: bool = True,
    sort_order: int = 10,
    short_name: str = "増田川",
    short_name_ascii: str = "MASUDA",
    display_code: str = "MDS",
) -> AppConfig:
    return AppConfig(
        sensors={
            "28-00000020f5ed": SensorConfig(
                sensor_id="28-00000020f5ed",
                name=name,
                type="water",
                offset=offset,
                min=18.0,
                max=28.0,
                role=role,
                enabled=enabled,
                visible=visible,
                sort_order=sort_order,
                short_name=short_name,
                short_name_ascii=short_name_ascii,
                display_code=display_code,
            )
        }
    )


def make_environment_config(*, visible: bool = True, enabled: bool = True) -> AppConfig:
    return AppConfig(
        sensors={},
        environment_sensors={
            "sht31_room": EnvironmentSensorConfig(
                sensor_key="sht31_room",
                name="室内",
                short_name="室内",
                short_name_ascii="ROOM",
                type="sht31",
                role="indoor",
                enabled=enabled,
                visible=visible,
                sort_order=200,
                i2c_bus=1,
                i2c_address=0x44,
                read_interval_seconds=60,
            )
        },
    )


def make_environment_reading(
    *,
    temperature_c: float | None = 25.0,
    humidity_percent: float | None = 56.0,
    crc_ok: bool = True,
    error: str | None = None,
) -> EnvironmentReading:
    return EnvironmentReading(
        sensor_key="sht31_room",
        name="室内",
        short_name="室内",
        short_name_ascii="ROOM",
        type="sht31",
        role="indoor",
        enabled=True,
        visible=True,
        sort_order=200,
        temperature_c=temperature_c,
        relative_humidity_percent=humidity_percent,
        crc_ok=crc_ok,
        error=error,
    )


def make_reading(
    sensor_id: str = "28-00000020f5ed",
    *,
    name: str = "増田川水槽",
    temperature_c: float | None = 23.187,
    status: str = "ok",
    crc_ok: bool = True,
) -> ConfiguredSensorReading:
    return ConfiguredSensorReading(
        sensor_id=sensor_id,
        name=name,
        type="water",
        raw_temperature_c=temperature_c,
        temperature_c=temperature_c,
        offset=0.0,
        min=18.0,
        max=28.0,
        status=status,
        crc_ok=crc_ok,
        raw="raw",
        error=None if crc_ok else "CRC チェックが失敗しました",
        role="aquarium",
        enabled=True,
        visible=True,
        sort_order=10,
        short_name="増田川",
        short_name_ascii="MASUDA",
        display_code="MDS",
    )


def make_weather(
    ts: datetime,
    *,
    temperature_c: float | None,
    precipitation_mm: float | None = 0.0,
) -> WeatherHourlyReading:
    return WeatherHourlyReading(
        ts=ts,
        source="open-meteo",
        latitude=35.681236,
        longitude=139.767125,
        temperature_c=temperature_c,
        relative_humidity_percent=63.0,
        wind_speed_ms=2.4,
        wind_direction_deg=180,
        precipitation_mm=precipitation_mm,
        snowfall_cm=0.0,
        cloud_cover_percent=80,
        surface_pressure_hpa=1007.2,
        shortwave_radiation=320,
        evapotranspiration_mm=0.12,
        soil_temperature_c=23.4,
        soil_moisture_m3_m3=0.31,
    )


if __name__ == "__main__":
    unittest.main()
