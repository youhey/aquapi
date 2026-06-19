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
                        "fans": [
                            {
                                "id": "fan_1",
                                "name": "Fan 1",
                                "gpio": 22,
                                "active_high": True,
                                "enabled": True,
                            }
                        ],
                        "environment_sensors": {
                            "sht31_room": {
                                "name": "室内",
                                "short_name": "室内",
                                "short_name_ascii": "ROOM",
                                "type": "sht31",
                                "role": "indoor",
                                "enabled": True,
                                "visible": True,
                                "sort_order": 200,
                                "i2c_bus": 1,
                                "i2c_address": "0x44",
                                "read_interval_seconds": 60,
                            }
                        },
                        "leak_sensors": {
                            "leak_main": {
                                "name": "漏水センサー",
                                "short_name": "漏水",
                                "short_name_ascii": "LEAK",
                                "type": "conductive_probe",
                                "role": "leak",
                                "enabled": True,
                                "visible": True,
                                "sort_order": 300,
                                "drive_gpio": 17,
                                "sense_gpio": 27,
                                "pull": "down",
                                "active_state": "high",
                                "read_interval_seconds": 5,
                                "debounce_seconds": 2,
                            }
                        },
                        "sensors": {
                            "28-00000020f5ed": {
                                "name": "増田川水槽",
                                "short_name": "増田川",
                                "short_name_ascii": "MASUDA",
                                "display_code": "MDS",
                                "type": "water",
                                "role": "aquarium",
                                "enabled": True,
                                "visible": True,
                                "sort_order": 10,
                                "offset": -0.2,
                                "min": 18.0,
                                "max": 28.0,
                                "temperature_alert": {
                                    "enabled": True,
                                    "too_hot_c": 30.0,
                                    "too_cold_c": 15.0,
                                },
                                "fan_control": {
                                    "enabled": True,
                                    "fan_id": "fan_1",
                                    "start_c": 28.0,
                                    "stop_c": 27.5,
                                },
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
        self.assertEqual(sensor_config.display_code, "MDS")
        self.assertEqual(sensor_config.type, "water")
        self.assertEqual(sensor_config.role, "aquarium")
        self.assertTrue(sensor_config.enabled)
        self.assertTrue(sensor_config.visible)
        self.assertEqual(sensor_config.sort_order, 10)
        self.assertEqual(sensor_config.offset, -0.2)
        self.assertEqual(sensor_config.min, 18.0)
        self.assertEqual(sensor_config.max, 28.0)
        self.assertTrue(sensor_config.temperature_alert.enabled)
        self.assertEqual(sensor_config.temperature_alert.too_hot_c, 30.0)
        self.assertEqual(sensor_config.temperature_alert.too_cold_c, 15.0)
        self.assertTrue(sensor_config.fan_control.enabled)
        self.assertEqual(sensor_config.fan_control.fan_id, "fan_1")
        self.assertEqual(sensor_config.fan_control.start_c, 28.0)
        self.assertEqual(sensor_config.fan_control.stop_c, 27.5)
        fan = config.configured_fans()["fan_1"]
        self.assertEqual(fan.name, "Fan 1")
        self.assertEqual(fan.gpio, 22)
        self.assertTrue(fan.active_high)
        self.assertTrue(fan.enabled)
        self.assertEqual(config.listen_addr, "127.0.0.1")
        self.assertEqual(config.listen_port, 8080)
        self.assertTrue(config.logging.enabled)
        self.assertEqual(config.logging.interval_seconds, 60)
        self.assertEqual(config.logging.storage, "sqlite")
        self.assertEqual(config.logging.database_path, Path("/var/lib/aquapi/aquapi.sqlite3"))
        self.assertEqual(config.logging.retention_days, 365)
        self.assertTrue(config.weather.enabled)
        self.assertEqual(config.weather.source, "open-meteo")
        environment_sensor = config.configured_environment_sensors()["sht31_room"]
        self.assertEqual(environment_sensor.name, "室内")
        self.assertEqual(environment_sensor.short_name_ascii, "ROOM")
        self.assertEqual(environment_sensor.type, "sht31")
        self.assertEqual(environment_sensor.role, "indoor")
        self.assertTrue(environment_sensor.enabled)
        self.assertTrue(environment_sensor.visible)
        self.assertEqual(environment_sensor.sort_order, 200)
        self.assertEqual(environment_sensor.i2c_bus, 1)
        self.assertEqual(environment_sensor.i2c_address, 0x44)
        self.assertEqual(environment_sensor.read_interval_seconds, 60)
        leak_sensor = config.configured_leak_sensors()["leak_main"]
        self.assertEqual(leak_sensor.name, "漏水センサー")
        self.assertEqual(leak_sensor.short_name, "漏水")
        self.assertEqual(leak_sensor.short_name_ascii, "LEAK")
        self.assertEqual(leak_sensor.type, "conductive_probe")
        self.assertEqual(leak_sensor.role, "leak")
        self.assertTrue(leak_sensor.enabled)
        self.assertTrue(leak_sensor.visible)
        self.assertEqual(leak_sensor.sort_order, 300)
        self.assertEqual(leak_sensor.drive_gpio, 17)
        self.assertEqual(leak_sensor.sense_gpio, 27)
        self.assertEqual(leak_sensor.pull, "down")
        self.assertEqual(leak_sensor.active_state, "high")
        self.assertEqual(leak_sensor.read_interval_seconds, 5)
        self.assertEqual(leak_sensor.debounce_seconds, 2)

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
        self.assertEqual(water.display_code, "")
        self.assertFalse(water.temperature_alert.enabled)
        self.assertFalse(water.fan_control.enabled)
        self.assertEqual(air.role, "outdoor")
        self.assertEqual(air.short_name, "外気")
        self.assertEqual(air.short_name_ascii, "")
        self.assertEqual(air.display_code, "")
        self.assertEqual(unknown.role, "unknown")
        self.assertEqual(unknown.short_name, "未分類")
        self.assertEqual(unknown.short_name_ascii, "")
        self.assertEqual(unknown.display_code, "")
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

    def test_load_config_rejects_duplicate_fan_id(self) -> None:
        config = {
            "fans": [
                {"id": "fan_1", "name": "Fan 1", "gpio": 22},
                {"id": "fan_1", "name": "Fan 1 duplicate", "gpio": 23},
            ],
            "sensors": {},
        }

        with TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "aquapi.json"
            config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "fan.id が重複"):
                load_config(config_path)

    def test_load_config_rejects_unknown_fan_binding(self) -> None:
        config = {
            "fans": [{"id": "fan_1", "name": "Fan 1", "gpio": 22}],
            "sensors": {
                "28-1": {
                    "name": "水槽",
                    "type": "water",
                    "fan_control": {
                        "enabled": True,
                        "fan_id": "fan_missing",
                        "start_c": 28.0,
                        "stop_c": 27.5,
                    },
                }
            },
        }

        with TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "aquapi.json"
            config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "fan_control.fan_id が fans に存在しません"):
                load_config(config_path)

    def test_load_config_rejects_invalid_fan_hysteresis(self) -> None:
        config = {
            "fans": [{"id": "fan_1", "name": "Fan 1", "gpio": 22}],
            "sensors": {
                "28-1": {
                    "name": "水槽",
                    "type": "water",
                    "fan_control": {
                        "enabled": True,
                        "fan_id": "fan_1",
                        "start_c": 27.5,
                        "stop_c": 28.0,
                    },
                }
            },
        }

        with TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "aquapi.json"
            config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "fan_control.start_c は stop_c より大きい"):
                load_config(config_path)

    def test_load_config_rejects_invalid_temperature_alert_range(self) -> None:
        config = {
            "sensors": {
                "28-1": {
                    "name": "水槽",
                    "type": "water",
                    "temperature_alert": {
                        "enabled": True,
                        "too_hot_c": 15.0,
                        "too_cold_c": 30.0,
                    },
                }
            }
        }

        with TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "aquapi.json"
            config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "temperature_alert.too_hot_c は too_cold_c より大きい"):
                load_config(config_path)

    def test_load_config_rejects_fan_control_on_non_water_sensor(self) -> None:
        config = {
            "fans": [{"id": "fan_1", "name": "Fan 1", "gpio": 22}],
            "sensors": {
                "28-air": {
                    "name": "外気",
                    "type": "air",
                    "fan_control": {
                        "enabled": True,
                        "fan_id": "fan_1",
                        "start_c": 28.0,
                        "stop_c": 27.5,
                    },
                }
            },
        }

        with TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "aquapi.json"
            config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "water 以外のセンサー"):
                load_config(config_path)

    def test_load_config_rejects_invalid_display_code(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "aquapi.json"
            config_path.write_text(
                json.dumps(
                    {
                        "sensors": {
                            "28-00000020f5ed": {
                                "name": "増田川水槽",
                                "type": "water",
                                "display_code": "MASUDA",
                                "offset": 0.0,
                            }
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "display_code は 3 文字"):
                load_config(config_path)

    def test_load_config_reads_integer_i2c_address(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "aquapi.json"
            config_path.write_text(
                json.dumps(
                    {
                        "sensors": {},
                        "environment_sensors": {
                            "sht31_room": {
                                "name": "ROOM",
                                "type": "sht31",
                                "i2c_address": 68,
                            }
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            config = load_config(config_path)

        environment_sensor = config.configured_environment_sensors()["sht31_room"]
        self.assertEqual(environment_sensor.i2c_address, 0x44)
        self.assertEqual(environment_sensor.i2c_bus, 1)
        self.assertEqual(environment_sensor.role, "indoor")
        self.assertEqual(environment_sensor.short_name, "ROOM")
        self.assertEqual(environment_sensor.short_name_ascii, "ROOM")

    def test_load_config_defaults_leak_gpio_values(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "aquapi.json"
            config_path.write_text(
                json.dumps(
                    {
                        "sensors": {},
                        "leak_sensors": {
                            "leak_main": {
                                "name": "LEAK",
                                "type": "conductive_probe",
                            }
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            config = load_config(config_path)

        leak_sensor = config.configured_leak_sensors()["leak_main"]
        self.assertEqual(leak_sensor.drive_gpio, 17)
        self.assertEqual(leak_sensor.sense_gpio, 27)
        self.assertEqual(leak_sensor.pull, "down")
        self.assertEqual(leak_sensor.active_state, "high")
        self.assertEqual(leak_sensor.read_interval_seconds, 5)
        self.assertEqual(leak_sensor.debounce_seconds, 2)
        self.assertEqual(leak_sensor.role, "leak")
        self.assertEqual(leak_sensor.short_name_ascii, "LEAK")

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
        self.assertEqual(sensor_config.display_code, "MDS")
        medaka = config.find_sensor("28-000000224fb6")
        mini = config.find_sensor("28-000000230ee6")
        kingyo = config.find_sensor("28-000000235d5e")
        outdoor = config.find_sensor("28-00000023733a")
        assert medaka is not None
        assert mini is not None
        assert kingyo is not None
        assert outdoor is not None
        self.assertEqual(medaka.display_code, "MDK")
        self.assertEqual(mini.display_code, "MIN")
        self.assertEqual(kingyo.display_code, "KNG")
        self.assertEqual(outdoor.display_code, "OUT")
        self.assertEqual(sorted(config.configured_fans()), ["fan_1", "fan_2", "fan_3", "fan_4"])
        self.assertEqual(sensor_config.fan_control.fan_id, "fan_1")
        self.assertTrue(sensor_config.temperature_alert.enabled)
        environment_sensor = config.configured_environment_sensors()["sht31_room"]
        self.assertEqual(environment_sensor.short_name_ascii, "ROOM")
        self.assertEqual(environment_sensor.i2c_address, 0x44)
        leak_sensor = config.configured_leak_sensors()["leak_main"]
        self.assertEqual(leak_sensor.short_name_ascii, "LEAK")
        self.assertEqual(leak_sensor.drive_gpio, 17)
        self.assertEqual(leak_sensor.sense_gpio, 27)

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
