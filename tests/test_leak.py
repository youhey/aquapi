from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from aquapi.config import AppConfig, LeakSensorConfig
from aquapi.leak import (
    LeakStateStore,
    debounced_leak_status,
    leak_status_from_raw,
    read_all_leak_sensors,
    read_leak_sensor,
)


class LeakTests(unittest.TestCase):
    def test_raw_high_and_low_status_mapping(self) -> None:
        self.assertEqual(leak_status_from_raw(1, active_state="high"), "wet")
        self.assertEqual(leak_status_from_raw(0, active_state="high"), "dry")
        self.assertEqual(leak_status_from_raw(0, active_state="low"), "wet")
        self.assertEqual(leak_status_from_raw(1, active_state="low"), "dry")

    def test_debounce_ignores_short_noise(self) -> None:
        state = LeakStateStore().state_for("leak_main")
        start = datetime(2026, 6, 9, 18, 0, tzinfo=timezone.utc)

        first = debounced_leak_status(
            state,
            raw_status="dry",
            measured_at=start,
            debounce_seconds=2,
        )
        noisy = debounced_leak_status(
            state,
            raw_status="wet",
            measured_at=start + timedelta(seconds=1),
            debounce_seconds=2,
        )
        confirmed = debounced_leak_status(
            state,
            raw_status="wet",
            measured_at=start + timedelta(seconds=3),
            debounce_seconds=2,
        )

        self.assertEqual(first, "dry")
        self.assertEqual(noisy, "dry")
        self.assertEqual(confirmed, "wet")

    def test_read_leak_sensor_returns_wet_for_active_high(self) -> None:
        sensor_config = make_leak_config(read_interval_seconds=1)
        now = datetime(2026, 6, 9, 18, 0, tzinfo=timezone.utc)

        with patch("aquapi.leak.read_leak_raw_gpio", return_value=1):
            reading = read_leak_sensor(sensor_config, state_store=LeakStateStore(), now=now)

        self.assertEqual(reading.status, "wet")
        self.assertTrue(reading.alert)
        self.assertEqual(reading.raw_value, 1)
        self.assertEqual(reading.measured_at, now)

    def test_read_leak_sensor_returns_unknown_on_gpio_failure(self) -> None:
        sensor_config = make_leak_config()

        with patch("aquapi.leak.read_leak_raw_gpio", side_effect=RuntimeError("gpio unavailable")):
            reading = read_leak_sensor(sensor_config, state_store=LeakStateStore())

        self.assertEqual(reading.status, "unknown")
        self.assertFalse(reading.alert)
        self.assertIsNone(reading.raw_value)
        self.assertEqual(reading.error, "gpio unavailable")

    def test_disabled_leak_sensor_is_not_read(self) -> None:
        config = AppConfig(
            sensors={},
            leak_sensors={
                "leak_main": make_leak_config(enabled=False),
            },
        )

        with patch("aquapi.leak.read_leak_raw_gpio") as read_raw:
            readings = read_all_leak_sensors(config)

        self.assertEqual(readings, [])
        read_raw.assert_not_called()


def make_leak_config(
    *,
    enabled: bool = True,
    active_state: str = "high",
    read_interval_seconds: int = 5,
) -> LeakSensorConfig:
    return LeakSensorConfig(
        sensor_key="leak_main",
        name="漏水センサー",
        short_name="漏水",
        short_name_ascii="LEAK",
        type="conductive_probe",
        role="leak",
        enabled=enabled,
        visible=True,
        sort_order=300,
        drive_gpio=17,
        sense_gpio=27,
        pull="down",
        active_state=active_state,
        read_interval_seconds=read_interval_seconds,
        debounce_seconds=2,
    )


if __name__ == "__main__":
    unittest.main()
