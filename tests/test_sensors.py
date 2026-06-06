from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from aquapi.sensors import SensorReadError, discover_sensor_paths, read_sensor


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


if __name__ == "__main__":
    unittest.main()
