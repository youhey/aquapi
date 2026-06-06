import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from aquapi.config import AppConfig, LoggingConfig
from aquapi.logs import (
    append_readings,
    build_history_summary_payload,
    build_series_payload,
    cleanup_old_logs,
    log_file_path,
    log_once,
)
from aquapi.sensors import ConfiguredSensorReading


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


class LogsTests(unittest.TestCase):
    def test_append_readings_writes_one_jsonl_line(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            logging_config = make_logging_config(Path(tmp_dir))
            result = append_readings(
                logging_config,
                [make_reading()],
                now=datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc),
            )

            lines = result.path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(len(lines), 1)
        payload = json.loads(lines[0])
        self.assertEqual(payload["sensors"][0]["sensor_id"], "28-00000020f5ed")
        self.assertEqual(payload["sensors"][0]["temperature_c"], 23.187)

    def test_log_file_path_uses_daily_file_name(self) -> None:
        logging_config = make_logging_config(Path("/tmp/aquapi-test"))

        path = log_file_path(
            logging_config,
            datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(path.name, "readings-2026-06-06.jsonl")

    def test_cleanup_old_logs_deletes_files_older_than_retention(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            data_dir = Path(tmp_dir)
            logging_config = make_logging_config(data_dir, retention_days=2)
            old_path = data_dir / "readings-2026-06-03.jsonl"
            keep_path = data_dir / "readings-2026-06-05.jsonl"
            old_path.write_text("{}\n", encoding="utf-8")
            keep_path.write_text("{}\n", encoding="utf-8")

            cleanup_old_logs(logging_config, today=datetime(2026, 6, 6).date())

            self.assertFalse(old_path.exists())
            self.assertTrue(keep_path.exists())

    def test_series_payload_returns_sensor_history(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            logging_config = make_logging_config(Path(tmp_dir))
            append_readings(
                logging_config,
                [make_reading()],
                now=datetime(2026, 6, 6, 11, 0, tzinfo=timezone.utc),
            )

            payload = build_series_payload(
                logging_config,
                sensor_id="28-00000020f5ed",
                range_text="24h",
                now=datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc),
            )

        assert payload is not None
        self.assertEqual(payload["sensor_id"], "28-00000020f5ed")
        self.assertEqual(payload["name"], "増田川水槽")
        self.assertEqual(payload["points"][0]["temperature_c"], 23.187)

    def test_history_summary_returns_min_avg_max_latest(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            logging_config = make_logging_config(Path(tmp_dir))
            append_readings(
                logging_config,
                [make_reading(temperature_c=22.0, status="low")],
                now=datetime(2026, 6, 6, 10, 0, tzinfo=timezone.utc),
            )
            append_readings(
                logging_config,
                [make_reading(temperature_c=24.0, status="ok")],
                now=datetime(2026, 6, 6, 11, 0, tzinfo=timezone.utc),
            )

            payload = build_history_summary_payload(
                logging_config,
                range_text="24h",
                now=datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc),
            )

        summary = payload["sensors"][0]
        self.assertEqual(summary["sample_count"], 2)
        self.assertEqual(summary["min_temperature_c"], 22.0)
        self.assertEqual(summary["avg_temperature_c"], 23.0)
        self.assertEqual(summary["max_temperature_c"], 24.0)
        self.assertEqual(summary["latest_temperature_c"], 24.0)
        self.assertEqual(summary["latest_status"], "ok")

    def test_invalid_range_raises_error(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            logging_config = make_logging_config(Path(tmp_dir))

            with self.assertRaisesRegex(ValueError, "unsupported range"):
                build_series_payload(logging_config, sensor_id="28-1", range_text="2h")

    def test_missing_sensor_returns_none(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            logging_config = make_logging_config(Path(tmp_dir))
            append_readings(logging_config, [make_reading(sensor_id="28-present")])

            payload = build_series_payload(
                logging_config,
                sensor_id="28-missing",
                range_text="24h",
            )

        self.assertIsNone(payload)

    def test_log_once_can_write_error_reading(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            logging_config = make_logging_config(Path(tmp_dir))
            config = AppConfig(sensors={}, logging=logging_config)

            result = log_once(
                config,
                readings=[make_reading(status="error", crc_ok=False, temperature_c=None)],
                now=datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc),
            )

            payload = json.loads(result.path.read_text(encoding="utf-8").splitlines()[0])

        self.assertEqual(payload["sensors"][0]["status"], "error")
        self.assertFalse(payload["sensors"][0]["crc_ok"])


def make_logging_config(
    data_dir: Path,
    *,
    retention_days: int = 30,
) -> LoggingConfig:
    data_dir.mkdir(parents=True, exist_ok=True)
    return LoggingConfig(
        enabled=True,
        interval_seconds=60,
        data_dir=data_dir,
        file_pattern="readings-%Y-%m-%d.jsonl",
        retention_days=retention_days,
    )


if __name__ == "__main__":
    unittest.main()

