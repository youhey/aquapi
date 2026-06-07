import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from aquapi.config import load_config


ROOT = Path(__file__).resolve().parents[1]


class ConfigTests(unittest.TestCase):
    def test_load_config_reads_sensor_mapping(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "aquapi.json"
            config_path.write_text(
                json.dumps(
                    {
                        "listen_addr": "127.0.0.1",
                        "listen_port": 8080,
                        "logging": {
                            "enabled": True,
                            "interval_seconds": 60,
                            "storage": "sqlite",
                            "database_path": "/var/lib/aquapi/aquapi.sqlite3",
                            "retention_days": 365,
                        },
                        "weather": {
                            "enabled": True,
                            "source": "open-meteo",
                            "latitude": 35.681236,
                            "longitude": 139.767125,
                            "timezone": "Asia/Tokyo",
                            "interval_seconds": 3600,
                            "forecast_days": 2,
                            "retention_days": 365,
                        },
                        "sensors": {
                            "28-00000020f5ed": {
                                "name": "増田川水槽",
                                "short_name": "増田川",
                                "short_name_ascii": "MASUDA",
                                "type": "water",
                                "role": "aquarium",
                                "enabled": True,
                                "visible": True,
                                "sort_order": 10,
                                "offset": -0.2,
                                "min": 18.0,
                                "max": 28.0,
                            }
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            config = load_config(config_path)

        sensor_config = config.find_sensor("28-00000020f5ed")
        self.assertIsNotNone(sensor_config)
        assert sensor_config is not None
        self.assertEqual(sensor_config.name, "増田川水槽")
        self.assertEqual(sensor_config.short_name, "増田川")
        self.assertEqual(sensor_config.short_name_ascii, "MASUDA")
        self.assertEqual(sensor_config.type, "water")
        self.assertEqual(sensor_config.role, "aquarium")
        self.assertTrue(sensor_config.enabled)
        self.assertTrue(sensor_config.visible)
        self.assertEqual(sensor_config.sort_order, 10)
        self.assertEqual(sensor_config.offset, -0.2)
        self.assertEqual(sensor_config.min, 18.0)
        self.assertEqual(sensor_config.max, 28.0)
        self.assertEqual(config.listen_addr, "127.0.0.1")
        self.assertEqual(config.listen_port, 8080)
        self.assertTrue(config.logging.enabled)
        self.assertEqual(config.logging.interval_seconds, 60)
        self.assertEqual(config.logging.storage, "sqlite")
        self.assertEqual(config.logging.database_path, Path("/var/lib/aquapi/aquapi.sqlite3"))
        self.assertEqual(config.logging.retention_days, 365)
        self.assertTrue(config.weather.enabled)
        self.assertEqual(config.weather.source, "open-meteo")

    def test_load_config_defaults_sensor_display_metadata(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "aquapi.json"
            config_path.write_text(
                json.dumps(
                    {
                        "sensors": {
                            "28-water": {
                                "name": "水槽",
                                "type": "water",
                                "offset": 0.0,
                            },
                            "28-air": {
                                "name": "外気",
                                "type": "air",
                                "offset": 0.0,
                            },
                            "28-unknown": {
                                "name": "未分類",
                                "type": "unknown",
                                "offset": 0.0,
                            },
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            config = load_config(config_path)

        water = config.find_sensor("28-water")
        air = config.find_sensor("28-air")
        unknown = config.find_sensor("28-unknown")
        assert water is not None
        assert air is not None
        assert unknown is not None
        self.assertEqual(water.role, "aquarium")
        self.assertEqual(water.short_name, "水槽")
        self.assertEqual(water.short_name_ascii, "")
        self.assertEqual(air.role, "outdoor")
        self.assertEqual(air.short_name, "外気")
        self.assertEqual(air.short_name_ascii, "")
        self.assertEqual(unknown.role, "unknown")
        self.assertEqual(unknown.short_name, "未分類")
        self.assertEqual(unknown.short_name_ascii, "")
        self.assertTrue(water.enabled)
        self.assertTrue(water.visible)
        self.assertEqual(water.sort_order, 1000)
        self.assertEqual(config.weather.latitude, 35.681236)
        self.assertEqual(config.weather.longitude, 139.767125)
        self.assertEqual(config.weather.timezone, "Asia/Tokyo")
        self.assertEqual(config.weather.interval_seconds, 3600)
        self.assertEqual(config.weather.forecast_days, 2)
        self.assertEqual(config.weather.retention_days, 365)

    def test_load_config_defaults_short_name_ascii_from_ascii_names(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "aquapi.json"
            config_path.write_text(
                json.dumps(
                    {
                        "sensors": {
                            "28-ascii-short": {
                                "name": "LongName",
                                "short_name": "SHORT",
                                "type": "water",
                                "offset": 0.0,
                            },
                            "28-ascii-name": {
                                "name": "OUTDOOR",
                                "type": "air",
                                "offset": 0.0,
                            },
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            config = load_config(config_path)

        ascii_short = config.find_sensor("28-ascii-short")
        ascii_name = config.find_sensor("28-ascii-name")
        assert ascii_short is not None
        assert ascii_name is not None
        self.assertEqual(ascii_short.short_name_ascii, "SHORT")
        self.assertEqual(ascii_name.short_name_ascii, "OUTDOOR")

    def test_load_config_defaults_api_listen_values(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "aquapi.json"
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

            config = load_config(config_path)

        self.assertEqual(config.listen_addr, "0.0.0.0")
        self.assertEqual(config.listen_port, 8080)
        self.assertFalse(config.logging.enabled)
        self.assertEqual(config.logging.interval_seconds, 60)
        self.assertEqual(config.logging.storage, "sqlite")
        self.assertEqual(config.logging.database_path, Path("data/aquapi.sqlite3"))
        self.assertEqual(config.logging.retention_days, 365)
        self.assertFalse(config.weather.enabled)
        self.assertEqual(config.weather.source, "open-meteo")

    def test_example_config_includes_short_name_ascii(self) -> None:
        config = load_config(ROOT / "configs/aquapi.example.json")

        sensor_config = config.find_sensor("28-00000020f5ed")
        assert sensor_config is not None
        self.assertEqual(sensor_config.short_name_ascii, "MASUDA")

    def test_load_config_can_select_jsonl_storage(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "aquapi.json"
            config_path.write_text(
                json.dumps(
                    {
                        "logging": {
                            "enabled": True,
                            "storage": "jsonl",
                            "data_dir": str(Path(tmp_dir) / "data"),
                            "file_pattern": "readings-%Y-%m-%d.jsonl",
                            "retention_days": 30,
                        },
                        "sensors": {},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            config = load_config(config_path)

        self.assertEqual(config.logging.storage, "jsonl")
        self.assertEqual(config.logging.retention_days, 30)

    def test_load_config_rejects_missing_sensors(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "aquapi.json"
            config_path.write_text("{}", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "sensors"):
                load_config(config_path)


if __name__ == "__main__":
    unittest.main()
