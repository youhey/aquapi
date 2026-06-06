import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from aquapi.config import AppConfig, LoggingConfig, SensorConfig
from aquapi.sqlite_storage import SQLiteStorage
from aquapi.sensors import ConfiguredSensorReading


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

        self.assertIn("sensors", tables)
        self.assertIn("readings", tables)
        self.assertIn("metadata", tables)
        self.assertEqual(schema_version, "1")

    def test_sync_sensors_inserts_and_updates_sensor_config(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            storage = SQLiteStorage(Path(tmp_dir) / "aquapi.sqlite3")
            storage.initialize()
            storage.sync_sensors(make_config(name="増田川水槽", offset=-0.2))
            storage.sync_sensors(make_config(name="更新後", offset=0.1))

            with sqlite3.connect(storage.database_path) as conn:
                row = conn.execute(
                    """
                    SELECT name, type, offset_milli_c, min_milli_c, max_milli_c
                    FROM sensors
                    WHERE device_id = '28-00000020f5ed'
                    """
                ).fetchone()

        self.assertEqual(row, ("更新後", "water", 100, 18000, 28000))

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


def make_config(*, name: str, offset: float) -> AppConfig:
    return AppConfig(
        sensors={
            "28-00000020f5ed": SensorConfig(
                sensor_id="28-00000020f5ed",
                name=name,
                type="water",
                offset=offset,
                min=18.0,
                max=28.0,
            )
        }
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
    )


if __name__ == "__main__":
    unittest.main()

