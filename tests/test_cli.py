import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from aquapi.cli import main
from aquapi.environment import EnvironmentReading


class CliTests(unittest.TestCase):
    def test_read_json_includes_name_and_status(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            config_path = tmp_path / "aquapi.json"
            sensor_path = tmp_path / "28-00000020f5ed"
            sensor_path.mkdir()
            (sensor_path / "w1_slave").write_text(
                """73 01 7f 80 7f ff 0d 10 ce : crc=ce YES
73 01 7f 80 7f ff 0d 10 ce t=23187
""",
                encoding="utf-8",
            )
            config_path.write_text(
                json.dumps(
                    {
                        "sensors": {
                            "28-00000020f5ed": {
                                "name": "増田川水槽",
                                "type": "water",
                                "offset": 0.0,
                                "min": 18.0,
                                "max": 28.0,
                            }
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with (
                patch("aquapi.cli.read_all_configured_sensors") as read_all,
                patch("sys.stdout", new_callable=StringIO) as stdout,
            ):
                from aquapi.config import load_config
                from aquapi.sensors import read_all_configured_sensors

                read_all.return_value = read_all_configured_sensors(
                    base_path=tmp_path,
                    config=load_config(config_path),
                )

                exit_code = main(["read", "--config", str(config_path), "--json"])

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["sensors"][0]["name"], "増田川水槽")
        self.assertEqual(payload["sensors"][0]["status"], "ok")
        self.assertEqual(payload["sensors"][0]["min"], 18.0)
        self.assertEqual(payload["sensors"][0]["max"], 28.0)

    def test_serve_uses_cli_host_and_port_over_config(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "aquapi.json"
            config_path.write_text(
                json.dumps(
                    {
                        "listen_addr": "0.0.0.0",
                        "listen_port": 8080,
                        "sensors": {
                            "28-00000020f5ed": {
                                "name": "増田川水槽",
                                "type": "water",
                                "offset": 0.0,
                                "min": 18.0,
                                "max": 28.0,
                            }
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with (
                patch("aquapi.cli.serve_api") as serve_api,
                patch("sys.stderr", new_callable=StringIO),
            ):
                exit_code = main(
                    [
                        "serve",
                        "--config",
                        str(config_path),
                        "--host",
                        "127.0.0.1",
                        "--port",
                        "18081",
                    ]
                )

        self.assertEqual(exit_code, 0)
        serve_api.assert_called_once()
        _, kwargs = serve_api.call_args
        self.assertEqual(kwargs["host"], "127.0.0.1")
        self.assertEqual(kwargs["port"], 18081)

    def test_read_environment_json_outputs_sht31_reading(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            config_path = write_sqlite_config(Path(tmp_dir))

            with (
                patch("aquapi.cli.read_all_environment_sensors") as read_all,
                patch("sys.stdout", new_callable=StringIO) as stdout,
            ):
                read_all.return_value = [
                    EnvironmentReading(
                        sensor_key="sht31_room",
                        name="室内",
                        short_name="室内",
                        short_name_ascii="ROOM",
                        type="sht31",
                        role="indoor",
                        enabled=True,
                        visible=True,
                        sort_order=200,
                        temperature_c=25.0,
                        relative_humidity_percent=56.0,
                        crc_ok=True,
                    )
                ]

                exit_code = main(["read-environment", "--config", str(config_path), "--json"])

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["sensors"][0]["sensor_key"], "sht31_room")
        self.assertEqual(payload["sensors"][0]["temperature_c"], 25.0)
        self.assertEqual(payload["sensors"][0]["relative_humidity_percent"], 56.0)

    def test_log_once_command_writes_log(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            config_path = tmp_path / "aquapi.json"
            data_dir = tmp_path / "data"
            config_path.write_text(
                json.dumps(
                    {
                        "logging": {
                            "enabled": True,
                            "interval_seconds": 60,
                            "storage": "sqlite",
                            "database_path": str(data_dir / "aquapi.sqlite3"),
                            "retention_days": 365,
                        },
                        "sensors": {},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with (
                patch("aquapi.cli.log_once") as log_once,
                patch("sys.stdout", new_callable=StringIO) as stdout,
            ):
                log_once.return_value.path = data_dir / "readings-2026-06-06.jsonl"
                log_once.return_value.entry = {"saved_count": 5}

                exit_code = main(["log-once", "--config", str(config_path)])

        self.assertEqual(exit_code, 0)
        log_once.assert_called_once()
        self.assertIn("Saved 5 readings", stdout.getvalue())

    def test_collect_command_calls_collector(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            config_path = tmp_path / "aquapi.json"
            config_path.write_text(
                json.dumps(
                    {
                        "logging": {
                            "enabled": True,
                            "interval_seconds": 60,
                            "storage": "sqlite",
                            "database_path": str(tmp_path / "data/aquapi.sqlite3"),
                            "retention_days": 365,
                        },
                        "sensors": {},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with patch("aquapi.cli.collect_forever") as collect_forever:
                exit_code = main(["collect", "--config", str(config_path)])

        self.assertEqual(exit_code, 0)
        collect_forever.assert_called_once()

    def test_db_init_command_initializes_storage(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            config_path = write_sqlite_config(Path(tmp_dir))

            with (
                patch("aquapi.cli.initialize_storage") as initialize_storage,
                patch("sys.stdout", new_callable=StringIO) as stdout,
            ):
                exit_code = main(["db-init", "--config", str(config_path)])

        self.assertEqual(exit_code, 0)
        initialize_storage.assert_called_once()
        self.assertIn("Initialized", stdout.getvalue())

    def test_db_stats_command_prints_counts(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            config_path = write_sqlite_config(Path(tmp_dir))

            with patch("sys.stdout", new_callable=StringIO) as stdout:
                exit_code = main(["db-stats", "--config", str(config_path)])

        self.assertEqual(exit_code, 0)
        self.assertIn("Database:", stdout.getvalue())
        self.assertIn("Readings:", stdout.getvalue())

    def test_fetch_weather_once_command_saves_weather(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            config_path = write_sqlite_config(Path(tmp_dir), weather_enabled=True)

            with (
                patch("aquapi.cli.fetch_weather_once") as fetch_weather_once,
                patch("sys.stdout", new_callable=StringIO) as stdout,
            ):
                fetch_weather_once.return_value.saved_count = 48
                fetch_weather_once.return_value.path = Path(tmp_dir) / "aquapi.sqlite3"

                exit_code = main(["fetch-weather-once", "--config", str(config_path)])

        self.assertEqual(exit_code, 0)
        fetch_weather_once.assert_called_once()
        self.assertIn("Saved 48 hourly weather records", stdout.getvalue())

    def test_weather_collect_command_calls_collector(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            config_path = write_sqlite_config(Path(tmp_dir), weather_enabled=True)

            with patch("aquapi.cli.collect_weather_forever") as collect_weather_forever:
                exit_code = main(["weather-collect", "--config", str(config_path)])

        self.assertEqual(exit_code, 0)
        collect_weather_forever.assert_called_once()

    def test_fetch_weather_once_rejects_disabled_weather(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            config_path = write_sqlite_config(Path(tmp_dir), weather_enabled=False)

            with patch("sys.stderr", new_callable=StringIO) as stderr:
                exit_code = main(["fetch-weather-once", "--config", str(config_path)])

        self.assertEqual(exit_code, 1)
        self.assertIn("weather is disabled", stderr.getvalue())

def write_sqlite_config(tmp_path: Path, *, weather_enabled: bool = False) -> Path:
    config_path = tmp_path / "aquapi.json"
    config_path.write_text(
        json.dumps(
            {
                "logging": {
                    "enabled": True,
                    "interval_seconds": 60,
                    "storage": "sqlite",
                    "database_path": str(tmp_path / "aquapi.sqlite3"),
                    "retention_days": 365,
                },
                "sensors": {},
                "weather": {
                    "enabled": weather_enabled,
                    "source": "open-meteo",
                    "latitude": 35.681236,
                    "longitude": 139.767125,
                    "timezone": "Asia/Tokyo",
                    "interval_seconds": 3600,
                    "forecast_days": 2,
                    "retention_days": 365,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return config_path


if __name__ == "__main__":
    unittest.main()
