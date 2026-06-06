import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from aquapi.config import load_config


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
                        "sensors": {
                            "28-00000020f5ed": {
                                "name": "増田川水槽",
                                "type": "water",
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
        self.assertEqual(sensor_config.type, "water")
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
