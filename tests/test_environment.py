from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from aquapi.config import AppConfig, EnvironmentSensorConfig
from aquapi.environment import (
    SHT31ReadError,
    collect_environment_if_due,
    read_all_environment_sensors,
    read_sht31_from_bus,
    sht31_crc,
    sht31_crc_ok,
    sht31_humidity_percent,
    sht31_temperature_c,
)


class FakeI2CBus:
    def __init__(self, response: list[int]):
        self.response = response
        self.writes: list[tuple[int, int, list[int]]] = []

    def write_i2c_block_data(self, i2c_addr: int, register: int, data: list[int]) -> None:
        self.writes.append((i2c_addr, register, data))

    def read_i2c_block_data(self, i2c_addr: int, register: int, length: int) -> list[int]:
        return self.response[:length]


class EnvironmentTests(unittest.TestCase):
    def test_sht31_crc_and_conversion(self) -> None:
        self.assertEqual(sht31_crc([0xBE, 0xEF]), 0x92)
        self.assertTrue(sht31_crc_ok([0xBE, 0xEF], 0x92))
        self.assertFalse(sht31_crc_ok([0xBE, 0xEF], 0x00))
        self.assertEqual(sht31_temperature_c(0), -45.0)
        self.assertEqual(sht31_temperature_c(65535), 130.0)
        self.assertEqual(sht31_humidity_percent(0), 0.0)
        self.assertEqual(sht31_humidity_percent(65535), 100.0)

    def test_read_sht31_from_bus_sends_single_shot_command_and_validates_crc(self) -> None:
        bus = FakeI2CBus([0x66, 0x66, 0x93, 0x80, 0x00, 0xA2])

        temperature_c, humidity_percent = read_sht31_from_bus(bus, 0x44)

        self.assertEqual(bus.writes, [(0x44, 0x24, [0x00])])
        self.assertAlmostEqual(temperature_c, 25.0, places=2)
        self.assertAlmostEqual(humidity_percent, 50.0, places=2)

    def test_read_sht31_from_bus_rejects_bad_crc(self) -> None:
        bus = FakeI2CBus([0x66, 0x66, 0x00, 0x80, 0x00, 0xA2])

        with self.assertRaisesRegex(SHT31ReadError, "temperature crc"):
            read_sht31_from_bus(bus, 0x44)

    def test_disabled_environment_sensor_is_not_read(self) -> None:
        config = AppConfig(
            sensors={},
            environment_sensors={
                "sht31_room": make_environment_config(enabled=False),
            },
        )

        with patch("aquapi.environment.read_environment_sensor") as read_sensor:
            readings = read_all_environment_sensors(config)

        self.assertEqual(readings, [])
        read_sensor.assert_not_called()

    def test_collect_environment_if_due_uses_smallest_enabled_interval(self) -> None:
        config = AppConfig(
            sensors={},
            environment_sensors={
                "slow": make_environment_config(sensor_key="slow", read_interval_seconds=300),
                "fast": make_environment_config(sensor_key="fast", read_interval_seconds=60),
            },
        )
        now = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)

        with patch("aquapi.environment.environment_log_once") as log_once:
            unchanged = collect_environment_if_due(
                config,
                now=now,
                last_read_at=now - timedelta(seconds=30),
            )
            changed = collect_environment_if_due(
                config,
                now=now,
                last_read_at=now - timedelta(seconds=60),
            )

        self.assertEqual(unchanged, now - timedelta(seconds=30))
        self.assertEqual(changed, now)
        log_once.assert_called_once()

    def test_collect_environment_if_due_skips_non_sqlite_logging(self) -> None:
        from aquapi.config import LoggingConfig

        config = AppConfig(
            sensors={},
            environment_sensors={
                "sht31_room": make_environment_config(),
            },
            logging=LoggingConfig(storage="jsonl"),
        )
        now = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)

        with patch("aquapi.environment.environment_log_once") as log_once:
            last_read_at = collect_environment_if_due(config, now=now, last_read_at=None)

        self.assertIsNone(last_read_at)
        log_once.assert_not_called()


def make_environment_config(
    *,
    sensor_key: str = "sht31_room",
    enabled: bool = True,
    read_interval_seconds: int = 60,
) -> EnvironmentSensorConfig:
    return EnvironmentSensorConfig(
        sensor_key=sensor_key,
        name="室内",
        short_name="室内",
        short_name_ascii="ROOM",
        type="sht31",
        role="indoor",
        enabled=enabled,
        visible=True,
        sort_order=200,
        i2c_bus=1,
        i2c_address=0x44,
        read_interval_seconds=read_interval_seconds,
    )


if __name__ == "__main__":
    unittest.main()
