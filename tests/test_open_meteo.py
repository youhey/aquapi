import json
import unittest
from unittest.mock import Mock, patch

from aquapi.config import WeatherConfig
from aquapi.open_meteo import (
    OpenMeteoError,
    build_open_meteo_url,
    fetch_open_meteo_hourly,
    parse_open_meteo_hourly,
)


class OpenMeteoTests(unittest.TestCase):
    def test_parse_hourly_maps_time_and_values(self) -> None:
        readings = parse_open_meteo_hourly(sample_payload(), WeatherConfig(enabled=True))

        self.assertEqual(len(readings), 2)
        self.assertEqual(readings[0].temperature_c, 25.1)
        self.assertEqual(readings[0].relative_humidity_percent, 63.0)
        self.assertEqual(readings[0].wind_direction_deg, 180)
        self.assertEqual(readings[0].soil_moisture_m3_m3, 0.31)
        self.assertEqual(readings[0].ts.utcoffset().total_seconds(), 9 * 60 * 60)
        self.assertIsNone(readings[1].relative_humidity_percent)

    def test_parse_rejects_missing_hourly(self) -> None:
        with self.assertRaisesRegex(OpenMeteoError, "hourly"):
            parse_open_meteo_hourly({}, WeatherConfig(enabled=True))

    def test_build_url_includes_config_values(self) -> None:
        url = build_open_meteo_url(
            WeatherConfig(
                enabled=True,
                latitude=35.681236,
                longitude=139.767125,
                timezone="Asia/Tokyo",
                forecast_days=2,
            )
        )

        self.assertIn("latitude=35.681236", url)
        self.assertIn("longitude=139.767125", url)
        self.assertIn("timezone=Asia%2FTokyo", url)
        self.assertIn("forecast_days=2", url)
        self.assertIn("wind_speed_unit=ms", url)

    def test_fetch_open_meteo_uses_json_response(self) -> None:
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=None)
        response.read.return_value = json.dumps(sample_payload()).encode("utf-8")

        with patch("aquapi.open_meteo.urlopen", return_value=response):
            readings = fetch_open_meteo_hourly(WeatherConfig(enabled=True))

        self.assertEqual(len(readings), 2)
        self.assertEqual(readings[0].source, "open-meteo")


def sample_payload() -> dict[str, object]:
    return {
        "hourly": {
            "time": ["2026-06-06T12:00", "2026-06-06T13:00"],
            "temperature_2m": [25.1, 26.2],
            "relative_humidity_2m": [63.0, None],
            "wind_speed_10m": [2.4, 2.8],
            "wind_direction_10m": [180, 190],
            "precipitation": [0.0, 1.2],
            "snowfall": [0.0, 0.0],
            "cloud_cover": [80, 90],
            "surface_pressure": [1007.2, 1006.5],
            "shortwave_radiation": [320, 250],
            "et0_fao_evapotranspiration": [0.12, 0.08],
            "soil_temperature_0cm": [23.4, 23.5],
            "soil_moisture_0_to_1cm": [0.31, 0.32],
        }
    }


if __name__ == "__main__":
    unittest.main()
