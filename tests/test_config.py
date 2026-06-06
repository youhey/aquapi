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
                        "listen_port": 8081,
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
        self.assertEqual(config.listen_port, 8081)

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
        self.assertEqual(config.listen_port, 8081)

    def test_load_config_rejects_missing_sensors(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "aquapi.json"
            config_path.write_text("{}", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "sensors"):
                load_config(config_path)


if __name__ == "__main__":
    unittest.main()
