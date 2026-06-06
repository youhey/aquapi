import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from aquapi.cli import main


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


if __name__ == "__main__":
    unittest.main()

