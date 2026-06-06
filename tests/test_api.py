import json
from pathlib import Path
from tempfile import TemporaryDirectory
from http import HTTPStatus
import unittest

from aquapi.api import ApiState, build_summary_payload, handle_api_request
from aquapi.config import AppConfig, LoggingConfig
from aquapi.logs import append_readings
from aquapi.sensors import ConfiguredSensorReading


def make_reading(
    sensor_id: str = "28-00000020f5ed",
    *,
    name: str = "増田川水槽",
    status: str = "ok",
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
    )


class ApiTests(unittest.TestCase):
    def test_health_returns_json(self) -> None:
        response = handle_api_request("/api/health", make_state([make_reading()]))

        self.assertEqual(response.status, HTTPStatus.OK)
        self.assertEqual(json.loads(json.dumps(response.payload))["service"], "aquapi")
        self.assertTrue(response.payload["ok"])
        self.assertEqual(response.payload["service"], "aquapi")
        self.assertEqual(response.payload["version"], "dev")

    def test_readings_returns_all_sensors(self) -> None:
        readings = [
            make_reading("28-00000020f5ed", name="増田川水槽"),
            make_reading("28-000000224fb6", name="めだか水槽"),
        ]

        response = handle_api_request("/api/readings", make_state(readings))

        self.assertEqual(response.status, HTTPStatus.OK)
        self.assertIn("generated_at", response.payload)
        sensors = response.payload["sensors"]
        self.assertIsInstance(sensors, list)
        self.assertEqual(len(sensors), 2)
        self.assertEqual(sensors[0]["name"], "増田川水槽")
        self.assertEqual(sensors[1]["sensor_id"], "28-000000224fb6")

    def test_summary_returns_status_counts(self) -> None:
        payload = build_summary_payload(
            [
                make_reading("28-1", status="ok"),
                make_reading("28-2", status="high"),
                make_reading("28-3", status="error"),
            ]
        )

        self.assertEqual(payload["counts"]["ok"], 1)
        self.assertEqual(payload["counts"]["high"], 1)
        self.assertEqual(payload["counts"]["error"], 1)
        self.assertEqual(payload["status"], "error")
        self.assertTrue(payload["alert"])

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
            append_readings(logging_config, [make_reading()])

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
            append_readings(logging_config, [make_reading(status="low")])
            append_readings(logging_config, [make_reading(status="ok")])

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


def make_state(
    readings: list[ConfiguredSensorReading],
    *,
    logging_config: LoggingConfig | None = None,
) -> ApiState:
    return ApiState(
        config=AppConfig(sensors={}, logging=logging_config or LoggingConfig()),
        readings_provider=lambda: readings,
    )


def make_logging_config(data_dir: Path) -> LoggingConfig:
    return LoggingConfig(
        enabled=True,
        interval_seconds=60,
        data_dir=data_dir,
        file_pattern="readings-%Y-%m-%d.jsonl",
        retention_days=30,
    )


if __name__ == "__main__":
    unittest.main()
