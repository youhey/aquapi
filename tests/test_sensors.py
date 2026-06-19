from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from aquapi.config import AppConfig, SensorConfig
from aquapi.sensors import (
    SensorReadError,
    apply_sensor_config,
    discover_sensor_paths,
    read_all_configured_sensors,
    read_sensor,
)


VALID_W1_SLAVE = """73 01 7f 80 7f ff 0d 10 ce : crc=ce YES
73 01 7f 80 7f ff 0d 10 ce t=23187
"""

CRC_NO_W1_SLAVE = """73 01 7f 80 7f ff 0d 10 ce : crc=ce NO
73 01 7f 80 7f ff 0d 10 ce t=23187
"""


def write_sensor(base_path: Path, sensor_id: str, body: str) -> Path:
    sensor_path = base_path / sensor_id
    sensor_path.mkdir()
    (sensor_path / "w1_slave").write_text(body, encoding="utf-8")
    return sensor_path


class SensorTests(unittest.TestCase):
    def test_read_sensor_parses_crc_yes_temperature_and_sensor_id(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            sensor_path = write_sensor(Path(tmp_dir), "28-00000020f5ed", VALID_W1_SLAVE)

            reading = read_sensor(sensor_path)

        self.assertEqual(reading.sensor_id, "28-00000020f5ed")
        self.assertTrue(reading.crc_ok)
        self.assertEqual(reading.temperature_c, 23.187)
        self.assertEqual(reading.raw, VALID_W1_SLAVE.strip())

    def test_read_sensor_rejects_crc_no(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            sensor_path = write_sensor(Path(tmp_dir), "28-000000224fb6", CRC_NO_W1_SLAVE)

            with self.assertRaisesRegex(SensorReadError, "CRC"):
                read_sensor(sensor_path)

    def test_read_sensor_rejects_missing_temperature(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            sensor_path = write_sensor(
                Path(tmp_dir),
                "28-000000230ee6",
                """73 01 7f 80 7f ff 0d 10 ce : crc=ce YES
73 01 7f 80 7f ff 0d 10 ce
""",
            )

            with self.assertRaisesRegex(SensorReadError, "t="):
                read_sensor(sensor_path)

    def test_discover_sensor_paths_returns_only_28_prefix_directories(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            sensor_a = write_sensor(tmp_path, "28-00000020f5ed", VALID_W1_SLAVE)
            sensor_b = write_sensor(tmp_path, "28-000000224fb6", VALID_W1_SLAVE)
            (tmp_path / "w1_bus_master1").mkdir()
            (tmp_path / "10-000000000000").mkdir()
            (tmp_path / "28-not-a-directory").write_text("", encoding="utf-8")

            self.assertEqual(discover_sensor_paths(tmp_path), [sensor_a, sensor_b])

    def test_apply_sensor_config_applies_offset_and_ok_status(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            sensor_path = write_sensor(Path(tmp_dir), "28-00000020f5ed", VALID_W1_SLAVE)
            reading = read_sensor(sensor_path)

        configured = apply_sensor_config(
            reading,
            SensorConfig(
                sensor_id="28-00000020f5ed",
                name="増田川水槽",
                type="water",
                offset=-0.2,
                min=18.0,
                max=28.0,
                display_code="MDS",
            ),
        )

        self.assertEqual(configured.raw_temperature_c, 23.187)
        self.assertAlmostEqual(configured.temperature_c, 22.987)
        self.assertEqual(configured.offset, -0.2)
        self.assertEqual(configured.status, "ok")
        self.assertEqual(configured.role, "aquarium")
        self.assertTrue(configured.enabled)
        self.assertTrue(configured.visible)
        self.assertEqual(configured.sort_order, 1000)
        self.assertEqual(configured.display_code, "MDS")

    def test_apply_sensor_config_returns_low_status(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            sensor_path = write_sensor(Path(tmp_dir), "28-00000020f5ed", VALID_W1_SLAVE)
            reading = read_sensor(sensor_path)

        configured = apply_sensor_config(
            reading,
            SensorConfig(
                sensor_id="28-00000020f5ed",
                name="増田川水槽",
                type="water",
                offset=0.0,
                min=24.0,
                max=28.0,
            ),
        )

        self.assertEqual(configured.status, "low")

    def test_apply_sensor_config_returns_high_status(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            sensor_path = write_sensor(Path(tmp_dir), "28-00000020f5ed", VALID_W1_SLAVE)
            reading = read_sensor(sensor_path)

        configured = apply_sensor_config(
            reading,
            SensorConfig(
                sensor_id="28-00000020f5ed",
                name="増田川水槽",
                type="water",
                offset=0.0,
                min=18.0,
                max=23.0,
            ),
        )

        self.assertEqual(configured.status, "high")

    def test_apply_sensor_config_returns_unknown_for_unregistered_sensor(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            sensor_path = write_sensor(Path(tmp_dir), "28-xxxxxxxxxxxx", VALID_W1_SLAVE)
            reading = read_sensor(sensor_path)

        configured = apply_sensor_config(reading, None)

        self.assertEqual(configured.name, "unknown")
        self.assertEqual(configured.type, "unknown")
        self.assertEqual(configured.status, "unknown")

    def test_read_all_configured_sensors_returns_error_for_crc_no(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            write_sensor(tmp_path, "28-000000224fb6", CRC_NO_W1_SLAVE)
            config = AppConfig(
                sensors={
                    "28-000000224fb6": SensorConfig(
                        sensor_id="28-000000224fb6",
                        name="めだか水槽",
                        type="water",
                        offset=0.0,
                        min=18.0,
                        max=28.0,
                    )
                }
            )

            readings = read_all_configured_sensors(tmp_path, config)

        self.assertEqual(readings[0].name, "めだか水槽")
        self.assertEqual(readings[0].status, "error")
        self.assertFalse(readings[0].crc_ok)

    def test_read_all_configured_sensors_skips_disabled_sensor(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            write_sensor(tmp_path, "28-enabled", VALID_W1_SLAVE)
            write_sensor(tmp_path, "28-disabled", VALID_W1_SLAVE)
            config = AppConfig(
                sensors={
                    "28-enabled": SensorConfig(
                        sensor_id="28-enabled",
                        name="有効",
                        type="water",
                        offset=0.0,
                        min=18.0,
                        max=28.0,
                        sort_order=10,
                    ),
                    "28-disabled": SensorConfig(
                        sensor_id="28-disabled",
                        name="無効",
                        type="water",
                        offset=0.0,
                        min=18.0,
                        max=28.0,
                        enabled=False,
                    ),
                }
            )

            readings = read_all_configured_sensors(tmp_path, config)

        self.assertEqual([reading.sensor_id for reading in readings], ["28-enabled"])


if __name__ == "__main__":
    unittest.main()
