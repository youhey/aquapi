import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from http import HTTPStatus
import unittest

from aquapi.api import ApiState, build_summary_payload, handle_api_request
from aquapi.config import AppConfig, LoggingConfig, SensorConfig
from aquapi.logs import write_readings
from aquapi.sqlite_storage import SQLiteStorage
from aquapi.sensors import ConfiguredSensorReading
from aquapi.weather import WeatherHourlyReading


def make_reading(
    sensor_id: str = "28-00000020f5ed",
    *,
    name: str = "増田川水槽",
    status: str = "ok",
    role: str = "aquarium",
    enabled: bool = True,
    visible: bool = True,
    sort_order: int = 1000,
) -> ConfiguredSensorReading:
    return ConfiguredSensorReading(
        sensor_id=sensor_id,
        name=name,
        type="water",
        raw_temperature_c=23.187,
        temperature_c=23.187,
        offset=0.0,
        min=18.0,
        max=28.0,
        status=status,
        crc_ok=True,
        raw="raw",
        role=role,
        enabled=enabled,
        visible=visible,
        sort_order=sort_order,
    )


class ApiTests(unittest.TestCase):
    def test_health_returns_json(self) -> None:
        response = handle_api_request("/api/health", make_state([make_reading()]))

        self.assertEqual(response.status, HTTPStatus.OK)
        self.assertEqual(json.loads(json.dumps(response.payload))["service"], "aquapi")
        self.assertTrue(response.payload["ok"])
        self.assertEqual(response.payload["service"], "aquapi")
        self.assertEqual(response.payload["version"], "dev")

    def test_readings_returns_visible_sensors_with_display_metadata_in_sort_order(self) -> None:
        readings = [
            make_reading("28-hidden", name="非表示", visible=False, sort_order=5),
            make_reading("28-second", name="めだか水槽", sort_order=20),
            make_reading("28-first", name="増田川水槽", sort_order=10),
        ]

        response = handle_api_request("/api/readings", make_state(readings))

        self.assertEqual(response.status, HTTPStatus.OK)
        self.assertIn("generated_at", response.payload)
        sensors = response.payload["sensors"]
        self.assertIsInstance(sensors, list)
        self.assertEqual(len(sensors), 2)
        self.assertEqual(sensors[0]["name"], "増田川水槽")
        self.assertEqual(sensors[0]["role"], "aquarium")
        self.assertTrue(sensors[0]["enabled"])
        self.assertTrue(sensors[0]["visible"])
        self.assertEqual(sensors[0]["sort_order"], 10)
        self.assertEqual(sensors[1]["sensor_id"], "28-second")

    def test_summary_returns_status_counts(self) -> None:
        payload = build_summary_payload(
            [
                make_reading("28-1", status="ok"),
                make_reading("28-2", status="high"),
                make_reading("28-3", status="error"),
                make_reading("28-hidden", status="error", visible=False),
            ]
        )

        self.assertEqual(payload["counts"]["ok"], 1)
        self.assertEqual(payload["counts"]["high"], 1)
        self.assertEqual(payload["counts"]["error"], 1)
        self.assertEqual(payload["status"], "error")
        self.assertTrue(payload["alert"])

    def test_sensors_returns_configured_sensor_master_in_sort_order(self) -> None:
        response = handle_api_request(
            "/api/sensors",
            make_state(
                [],
                sensors={
                    "28-second": SensorConfig(
                        sensor_id="28-second",
                        name="B",
                        type="water",
                        offset=0.0,
                        min=18.0,
                        max=28.0,
                        sort_order=20,
                    ),
                    "28-first": SensorConfig(
                        sensor_id="28-first",
                        name="A",
                        type="air",
                        offset=0.1,
                        min=5.0,
                        max=35.0,
                        role="outdoor",
                        enabled=False,
                        visible=False,
                        sort_order=10,
                    ),
                },
            ),
        )

        self.assertEqual(response.status, HTTPStatus.OK)
        sensors = response.payload["sensors"]
        self.assertEqual([sensor["sensor_id"] for sensor in sensors], ["28-first", "28-second"])
        self.assertEqual(sensors[0]["role"], "outdoor")
        self.assertFalse(sensors[0]["enabled"])
        self.assertFalse(sensors[0]["visible"])
        self.assertEqual(sensors[0]["offset"], 0.1)

    def test_sensor_detail_returns_matching_sensor(self) -> None:
        response = handle_api_request("/api/sensors/28-00000020f5ed", make_state([make_reading()]))

        self.assertEqual(response.status, HTTPStatus.OK)
        self.assertEqual(response.payload["sensor_id"], "28-00000020f5ed")
        self.assertEqual(response.payload["name"], "増田川水槽")
        self.assertEqual(response.payload["temperature_c"], 23.187)
        self.assertEqual(response.payload["status"], "ok")

    def test_sensor_detail_returns_json_404_for_missing_sensor(self) -> None:
        response = handle_api_request("/api/sensors/28-missing", make_state([make_reading()]))

        self.assertEqual(response.status, HTTPStatus.NOT_FOUND)
        self.assertEqual(response.payload["error"]["code"], "sensor_not_found")
        self.assertEqual(response.payload["error"]["sensor_id"], "28-missing")

    def test_series_api_returns_history_points(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            logging_config = make_logging_config(Path(tmp_dir))
            write_readings(AppConfig(sensors={}, logging=logging_config), [make_reading()])

            response = handle_api_request(
                "/api/readings/series",
                make_state([], logging_config=logging_config),
                query={"sensor_id": "28-00000020f5ed", "range": "24h"},
            )

        self.assertEqual(response.status, HTTPStatus.OK)
        self.assertEqual(response.payload["sensor_id"], "28-00000020f5ed")
        self.assertEqual(response.payload["points"][0]["status"], "ok")

    def test_summary_history_api_returns_aggregates(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            logging_config = make_logging_config(Path(tmp_dir))
            config = AppConfig(sensors={}, logging=logging_config)
            now = datetime.now(timezone.utc)
            write_readings(config, [make_reading(status="low")], now=now - timedelta(seconds=1))
            write_readings(config, [make_reading(status="ok")], now=now)

            response = handle_api_request(
                "/api/readings/summary",
                make_state([], logging_config=logging_config),
                query={"range": "24h"},
            )

        self.assertEqual(response.status, HTTPStatus.OK)
        summary = response.payload["sensors"][0]
        self.assertEqual(summary["sample_count"], 2)
        self.assertEqual(summary["latest_status"], "ok")

    def test_series_api_returns_400_for_invalid_range(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            response = handle_api_request(
                "/api/readings/series",
                make_state([], logging_config=make_logging_config(Path(tmp_dir))),
                query={"sensor_id": "28-00000020f5ed", "range": "2h"},
            )

        self.assertEqual(response.status, HTTPStatus.BAD_REQUEST)
        self.assertEqual(response.payload["error"]["code"], "invalid_range")

    def test_series_api_returns_404_for_missing_sensor_history(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            response = handle_api_request(
                "/api/readings/series",
                make_state([], logging_config=make_logging_config(Path(tmp_dir))),
                query={"sensor_id": "28-missing", "range": "24h"},
            )

        self.assertEqual(response.status, HTTPStatus.NOT_FOUND)
        self.assertEqual(response.payload["error"]["code"], "sensor_not_found")

    def test_weather_latest_returns_latest_record(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            logging_config = make_logging_config(Path(tmp_dir))
            storage = SQLiteStorage(logging_config.database_path)
            now = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
            storage.insert_weather_hourly([make_weather(now)], now)

            response = handle_api_request(
                "/api/weather/latest",
                make_state([], logging_config=logging_config, weather_enabled=True),
            )

        self.assertEqual(response.status, HTTPStatus.OK)
        weather = response.payload["weather"]
        self.assertEqual(weather["temperature_c"], 25.1)
        self.assertEqual(weather["source"], "open-meteo")

    def test_weather_series_returns_points(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            logging_config = make_logging_config(Path(tmp_dir))
            storage = SQLiteStorage(logging_config.database_path)
            now = datetime.now(timezone.utc)
            storage.insert_weather_hourly([make_weather(now - timedelta(minutes=5))], now)

            response = handle_api_request(
                "/api/weather/series",
                make_state([], logging_config=logging_config, weather_enabled=True),
                query={"range": "24h"},
            )

        self.assertEqual(response.status, HTTPStatus.OK)
        self.assertEqual(response.payload["points"][0]["temperature_c"], 25.1)

    def test_weather_summary_returns_aggregates(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            logging_config = make_logging_config(Path(tmp_dir))
            storage = SQLiteStorage(logging_config.database_path)
            now = datetime.now(timezone.utc)
            storage.insert_weather_hourly(
                [
                    make_weather(now - timedelta(hours=2), temperature_c=20.0, precipitation_mm=1.2),
                    make_weather(now - timedelta(hours=1), temperature_c=26.0, precipitation_mm=0.8),
                ],
                now,
            )

            response = handle_api_request(
                "/api/weather/summary",
                make_state([], logging_config=logging_config, weather_enabled=True),
                query={"range": "24h"},
            )

        self.assertEqual(response.status, HTTPStatus.OK)
        self.assertEqual(response.payload["sample_count"], 2)
        self.assertEqual(response.payload["temperature"]["avg_c"], 23.0)
        self.assertEqual(response.payload["precipitation"]["total_mm"], 2.0)

    def test_weather_disabled_returns_error(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            response = handle_api_request(
                "/api/weather/latest",
                make_state([], logging_config=make_logging_config(Path(tmp_dir)), weather_enabled=False),
            )

        self.assertEqual(response.status, HTTPStatus.SERVICE_UNAVAILABLE)
        self.assertEqual(response.payload["error"]["code"], "weather_disabled")

    def test_weather_latest_missing_returns_404(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            response = handle_api_request(
                "/api/weather/latest",
                make_state([], logging_config=make_logging_config(Path(tmp_dir)), weather_enabled=True),
            )

        self.assertEqual(response.status, HTTPStatus.NOT_FOUND)
        self.assertEqual(response.payload["error"]["code"], "weather_not_found")


def make_state(
    readings: list[ConfiguredSensorReading],
    *,
    logging_config: LoggingConfig | None = None,
    weather_enabled: bool = False,
    sensors: dict[str, SensorConfig] | None = None,
) -> ApiState:
    from aquapi.config import WeatherConfig

    return ApiState(
        config=AppConfig(
            sensors=sensors or {},
            logging=logging_config or LoggingConfig(),
            weather=WeatherConfig(enabled=weather_enabled),
        ),
        readings_provider=lambda: readings,
    )


def make_logging_config(data_dir: Path) -> LoggingConfig:
    return LoggingConfig(
        enabled=True,
        interval_seconds=60,
        storage="sqlite",
        database_path=data_dir / "aquapi.sqlite3",
        retention_days=365,
    )


def make_weather(
    ts: datetime,
    *,
    temperature_c: float | None = 25.1,
    precipitation_mm: float | None = 0.0,
) -> WeatherHourlyReading:
    return WeatherHourlyReading(
        ts=ts,
        source="open-meteo",
        latitude=35.681236,
        longitude=139.767125,
        temperature_c=temperature_c,
        relative_humidity_percent=63.0,
        wind_speed_ms=2.4,
        wind_direction_deg=180,
        precipitation_mm=precipitation_mm,
        snowfall_cm=0.0,
        cloud_cover_percent=80,
        surface_pressure_hpa=1007.2,
        shortwave_radiation=320,
        evapotranspiration_mm=0.12,
        soil_temperature_c=23.4,
        soil_moisture_m3_m3=0.31,
    )


if __name__ == "__main__":
    unittest.main()
