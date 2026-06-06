from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SystemdUnitTests(unittest.TestCase):
    def test_api_unit_uses_installed_venv_and_config_path(self) -> None:
        body = (ROOT / "deploy/systemd/aquapi-api.service").read_text(encoding="utf-8")

        self.assertIn("User=aquapi", body)
        self.assertIn("WorkingDirectory=/opt/aquapi", body)
        self.assertIn("Environment=AQUAPI_CONFIG=/etc/aquapi/aquapi.json", body)
        self.assertIn("ExecStart=/opt/aquapi/.venv/bin/python -m aquapi.cli serve", body)

    def test_collect_unit_runs_collector(self) -> None:
        body = (ROOT / "deploy/systemd/aquapi-collect.service").read_text(encoding="utf-8")

        self.assertIn("User=aquapi", body)
        self.assertIn("WorkingDirectory=/opt/aquapi", body)
        self.assertIn("ExecStart=/opt/aquapi/.venv/bin/python -m aquapi.cli collect", body)
        self.assertIn("Restart=always", body)

    def test_weather_unit_runs_weather_collector(self) -> None:
        body = (ROOT / "deploy/systemd/aquapi-weather.service").read_text(encoding="utf-8")

        self.assertIn("User=aquapi", body)
        self.assertIn("WorkingDirectory=/opt/aquapi", body)
        self.assertIn("ExecStart=/opt/aquapi/.venv/bin/python -m aquapi.cli weather-collect", body)
        self.assertIn("After=network-online.target", body)


if __name__ == "__main__":
    unittest.main()
