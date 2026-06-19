import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from http import HTTPStatus
import unittest

from aquapi.api import ApiState, build_summary_payload, handle_api_request
from aquapi.config import (
    AppConfig,
    EnvironmentSensorConfig,
    FanConfig,
    FanControlConfig,
    LoggingConfig,
    SensorConfig,
    TemperatureAlertConfig,
)
from aquapi.environment import EnvironmentReading
from aquapi.fans import FanState, FanStateStore
from aquapi.leak import LeakReading
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
    short_name: str = "増田川",
    short_name_ascii: str = "MASUDA",
    display_code: str = "MDS",
    temperature_alert: TemperatureAlertConfig | None = None,
    fan_control: FanControlConfig | None = None,
    sensor_type: str = "water",
    temperature_c: float | None = 23.187,
    min_temperature: float | None = 18.0,
    max_temperature: float | None = 28.0,
    crc_ok: bool = True,
    error: str | None = None,
) -> ConfiguredSensorReading:
    return ConfiguredSensorReading(
        sensor_id=sensor_id,
        name=name,
        type=sensor_type,
        raw_temperature_c=temperature_c,
        temperature_c=temperature_c,
        offset=0.0,
        min=min_temperature,
        max=max_temperature,
        status=status,
        crc_ok=crc_ok,
        raw="raw",
        error=error,
        role=role,
        enabled=enabled,
        visible=visible,
        sort_order=sort_order,
        short_name=short_name,
        short_name_ascii=short_name_ascii,
        display_code=display_code,
        temperature_alert=temperature_alert or TemperatureAlertConfig(),
        fan_control=fan_control or FanControlConfig(),
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
        self.assertEqual(sensors[0]["short_name"], "増田川")
        self.assertEqual(sensors[0]["short_name_ascii"], "MASUDA")
        self.assertEqual(sensors[0]["display_code"], "MDS")
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

    def test_summary_includes_temperature_alert_and_fan_state(self) -> None:
        response = handle_api_request(
            "/api/summary",
            make_state(
                [
                    make_reading(
                        temperature_c=28.1,
                        temperature_alert=TemperatureAlertConfig(
                            enabled=True,
                            too_hot_c=30.0,
                            too_cold_c=15.0,
                        ),
                        fan_control=FanControlConfig(
                            enabled=True,
                            fan_id="fan_1",
                            start_c=28.0,
                            stop_c=27.5,
                        ),
                    )
                ],
                fans=make_fans(),
                fan_states=[make_fan_state(state="on", reason="temperature_above_start")],
            ),
        )

        self.assertEqual(response.status, HTTPStatus.OK)
        tank = response.payload["tanks"][0]
        self.assertEqual(tank["temperature_alert"]["state"], "ok")
        self.assertEqual(tank["temperature_alert"]["too_hot_c"], 30.0)
        self.assertEqual(tank["fan_control"]["fan_id"], "fan_1")
        self.assertEqual(tank["fan_control"]["state"], "on")
        self.assertEqual(tank["fan_mode"], "auto")
        self.assertEqual(tank["fan_reason"], "temperature_above_start")
        self.assertEqual(response.payload["fans"][0]["state"], "on")
        self.assertEqual(response.payload["fans"][0]["mode"], "auto")

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
                        short_name_ascii="B",
                        display_code="BBB",
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
                        short_name_ascii="A",
                        display_code="AAA",
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
        self.assertEqual(sensors[0]["short_name"], "A")
        self.assertEqual(sensors[0]["short_name_ascii"], "A")
        self.assertEqual(sensors[0]["display_code"], "AAA")

    def test_monitoring_compact_returns_ok_for_safe_aquariums(self) -> None:
        response = handle_api_request(
            "/api/monitoring/compact",
            make_state(
                [
                    make_reading(
                        "28-second",
                        name="めだか水槽",
                        short_name="めだか",
                        short_name_ascii="MEDAKA",
                        display_code="MDK",
                        temperature_c=21.4,
                        sort_order=20,
                    ),
                    make_reading(
                        "28-first",
                        name="増田川水槽",
                        short_name="増田川",
                        short_name_ascii="MASUDA",
                        display_code="MDS",
                        temperature_c=21.4,
                        sort_order=10,
                    ),
                ]
            ),
        )

        self.assertEqual(response.status, HTTPStatus.OK)
        self.assertEqual(response.payload["level"], "ok")
        self.assertEqual(response.payload["label"], "AQUA OK")
        self.assertFalse(response.payload["alert"])
        self.assertEqual(response.payload["issue_count"], 0)
        tanks = response.payload["tanks"]
        self.assertEqual([tank["sensor_id"] for tank in tanks], ["28-first", "28-second"])
        self.assertEqual(tanks[0]["short_name"], "増田川")
        self.assertEqual(tanks[0]["short_name_ascii"], "MASUDA")
        self.assertEqual(tanks[0]["display_code"], "MDS")
        self.assertEqual(tanks[0]["temperature_c"], 21.4)
        self.assertEqual(tanks[0]["status"], "safety")
        self.assertFalse(tanks[0]["alert"])

    def test_monitoring_compact_includes_temperature_alert_and_fan_state(self) -> None:
        response = handle_api_request(
            "/api/monitoring/compact",
            make_state(
                [
                    make_reading(
                        temperature_c=28.1,
                        temperature_alert=TemperatureAlertConfig(
                            enabled=True,
                            too_hot_c=30.0,
                            too_cold_c=15.0,
                        ),
                        fan_control=FanControlConfig(
                            enabled=True,
                            fan_id="fan_1",
                            start_c=28.0,
                            stop_c=27.5,
                        ),
                    )
                ],
                fans=make_fans(),
                fan_states=[make_fan_state(state="on", reason="temperature_above_start")],
            ),
        )

        self.assertEqual(response.status, HTTPStatus.OK)
        tank = response.payload["tanks"][0]
        self.assertEqual(tank["temperature_alert_state"], "ok")
        self.assertEqual(tank["fan_id"], "fan_1")
        self.assertEqual(tank["fan_state"], "on")
        self.assertEqual(tank["fan_mode"], "auto")
        self.assertEqual(response.payload["fans"], [{"id": "fan_1", "state": "on", "mode": "auto", "enabled": True}])

    def test_fans_endpoint_returns_current_mode_and_bound_tank_metadata(self) -> None:
        response = handle_api_request(
            "/api/fans",
            make_state(
                [
                    make_reading(
                        temperature_c=28.1,
                        fan_control=FanControlConfig(
                            enabled=True,
                            fan_id="fan_1",
                            start_c=28.0,
                            stop_c=27.5,
                        ),
                    )
                ],
                sensors=make_bound_sensor_configs(),
                fans=make_fans(),
                fan_states=[make_fan_state(state="on", reason="temperature_above_start")],
            ),
        )

        self.assertEqual(response.status, HTTPStatus.OK)
        fan = response.payload["fans"][0]
        self.assertEqual(fan["id"], "fan_1")
        self.assertEqual(fan["mode"], "auto")
        self.assertEqual(fan["state"], "on")
        self.assertEqual(fan["bound_tank_id"], "28-00000020f5ed")
        self.assertEqual(fan["bound_tank_name"], "増田川水槽")
        self.assertEqual(fan["bound_tank_display_code"], "MDS")

    def test_fan_manual_control_api_persists_manual_on_mode(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            store = FanStateStore(Path(tmp_dir) / "fan-state.json")
            state = make_state(
                [
                    make_reading(
                        temperature_c=20.0,
                        fan_control=FanControlConfig(
                            enabled=True,
                            fan_id="fan_1",
                            start_c=28.0,
                            stop_c=27.5,
                        ),
                    )
                ],
                sensors=make_bound_sensor_configs(),
                fans=make_fans(),
                fan_state_store=store,
            )

            response = handle_api_request("/api/fans/fan_1/manual-on", state, method="POST")
            loaded = store.load(state.config)["fan_1"]

        self.assertEqual(response.status, HTTPStatus.OK)
        self.assertEqual(response.payload["fan"]["mode"], "manual_on")
        self.assertEqual(response.payload["fan"]["state"], "on")
        self.assertEqual(response.payload["fan"]["reason"], "manual_on")
        self.assertEqual(loaded.mode, "manual_on")

    def test_fan_manual_control_api_persists_manual_off_mode(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            store = FanStateStore(Path(tmp_dir) / "fan-state.json")
            state = make_state(
                [
                    make_reading(
                        temperature_c=30.0,
                        fan_control=FanControlConfig(
                            enabled=True,
                            fan_id="fan_1",
                            start_c=28.0,
                            stop_c=27.5,
                        ),
                    )
                ],
                sensors=make_bound_sensor_configs(),
                fans=make_fans(),
                fan_state_store=store,
            )

            response = handle_api_request("/api/fans/fan_1/manual-off", state, method="POST")
            loaded = store.load(state.config)["fan_1"]

        self.assertEqual(response.status, HTTPStatus.OK)
        self.assertEqual(response.payload["fan"]["mode"], "manual_off")
        self.assertEqual(response.payload["fan"]["state"], "off")
        self.assertEqual(response.payload["fan"]["reason"], "manual_off")
        self.assertEqual(loaded.mode, "manual_off")

    def test_fan_auto_api_resumes_temperature_control(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            store = FanStateStore(Path(tmp_dir) / "fan-state.json")
            state = make_state(
                [
                    make_reading(
                        temperature_c=27.5,
                        fan_control=FanControlConfig(
                            enabled=True,
                            fan_id="fan_1",
                            start_c=28.0,
                            stop_c=27.5,
                        ),
                    )
                ],
                sensors=make_bound_sensor_configs(),
                fans=make_fans(),
                fan_state_store=store,
            )
            handle_api_request("/api/fans/fan_1/manual-on", state, method="POST")

            response = handle_api_request("/api/fans/fan_1/auto", state, method="POST")
            loaded = store.load(state.config)["fan_1"]

        self.assertEqual(response.status, HTTPStatus.OK)
        self.assertEqual(response.payload["fan"]["mode"], "auto")
        self.assertEqual(response.payload["fan"]["state"], "off")
        self.assertEqual(response.payload["fan"]["reason"], "temperature_below_stop")
        self.assertEqual(loaded.mode, "auto")

    def test_fan_manual_control_api_returns_404_for_missing_fan(self) -> None:
        response = handle_api_request(
            "/api/fans/not_found/manual-on",
            make_state([], fans=make_fans()),
            method="POST",
        )

        self.assertEqual(response.status, HTTPStatus.NOT_FOUND)
        self.assertEqual(response.payload["error"], "fan_not_found")

    def test_fan_manual_on_api_rejects_disabled_fan(self) -> None:
        response = handle_api_request(
            "/api/fans/fan_1/manual-on",
            make_state([], fans=make_fans(enabled=False)),
            method="POST",
        )

        self.assertEqual(response.status, HTTPStatus.CONFLICT)
        self.assertEqual(response.payload["error"], "fan_disabled")

    def test_tanks_latest_returns_only_visible_aquarium_status(self) -> None:
        response = handle_api_request(
            "/api/tanks/latest",
            make_state(
                [
                    make_reading("28-hidden", name="非表示", visible=False, sort_order=5),
                    make_reading("28-outdoor", name="外気", role="outdoor", sensor_type="air"),
                    make_reading(
                        "28-warning",
                        name="めだか水槽",
                        short_name="めだか",
                        short_name_ascii="MEDAKA",
                        display_code="MDK",
                        temperature_c=29.2,
                        sort_order=20,
                    ),
                    make_reading(
                        "28-safe",
                        name="増田川水槽",
                        short_name="増田川",
                        short_name_ascii="MASUDA",
                        display_code="MDS",
                        temperature_c=21.4,
                        sort_order=10,
                    ),
                ]
            ),
        )

        self.assertEqual(response.status, HTTPStatus.OK)
        self.assertIn("generated_at", response.payload)
        tanks = response.payload["tanks"]
        self.assertEqual([tank["sensor_id"] for tank in tanks], ["28-safe", "28-warning"])
        self.assertEqual(tanks[0]["display_code"], "MDS")
        self.assertEqual(tanks[1]["display_code"], "MDK")
        self.assertEqual(tanks[0]["status"], "safety")
        self.assertFalse(tanks[0]["alert"])
        self.assertEqual(tanks[1]["status"], "warning")
        self.assertTrue(tanks[1]["alert"])

    def test_tanks_latest_can_include_hidden_aquariums(self) -> None:
        response = handle_api_request(
            "/api/tanks/latest",
            make_state(
                [
                    make_reading("28-hidden", name="非表示", visible=False, sort_order=5),
                    make_reading("28-visible", name="表示", sort_order=10),
                ]
            ),
            query={"include_hidden": "true"},
        )

        tanks = response.payload["tanks"]
        self.assertEqual([tank["sensor_id"] for tank in tanks], ["28-hidden", "28-visible"])

    def test_monitoring_compact_warns_for_small_range_deviation(self) -> None:
        response = handle_api_request(
            "/api/monitoring/compact",
            make_state([make_reading(temperature_c=29.2, max_temperature=28.0)]),
        )

        self.assertEqual(response.status, HTTPStatus.OK)
        self.assertEqual(response.payload["level"], "warning")
        self.assertEqual(response.payload["label"], "WARN")
        self.assertEqual(response.payload["issue_count"], 1)
        self.assertEqual(response.payload["tanks"][0]["status"], "warning")
        self.assertTrue(response.payload["tanks"][0]["alert"])

    def test_monitoring_compact_warns_for_small_low_deviation(self) -> None:
        response = handle_api_request(
            "/api/monitoring/compact",
            make_state([make_reading(temperature_c=16.5, min_temperature=18.0)]),
        )

        self.assertEqual(response.payload["level"], "warning")
        self.assertEqual(response.payload["tanks"][0]["status"], "warning")

    def test_monitoring_compact_marks_large_deviation_as_critical(self) -> None:
        response = handle_api_request(
            "/api/monitoring/compact",
            make_state([make_reading(temperature_c=31.1, max_temperature=28.0)]),
        )

        self.assertEqual(response.status, HTTPStatus.OK)
        self.assertEqual(response.payload["level"], "critical")
        self.assertEqual(response.payload["label"], "DANGER")
        self.assertEqual(response.payload["title"], "Dangerous aquarium temperature")
        self.assertEqual(response.payload["tanks"][0]["status"], "danger")

    def test_monitoring_compact_marks_large_low_deviation_as_critical(self) -> None:
        response = handle_api_request(
            "/api/monitoring/compact",
            make_state([make_reading(temperature_c=15.9, min_temperature=18.0)]),
        )

        self.assertEqual(response.payload["level"], "critical")
        self.assertEqual(response.payload["tanks"][0]["status"], "danger")

    def test_monitoring_compact_returns_unknown_for_unavailable_status(self) -> None:
        cases = [
            make_reading("28-temp", temperature_c=None),
            make_reading("28-min", min_temperature=None),
            make_reading("28-max", max_temperature=None),
            make_reading("28-crc", crc_ok=False),
            make_reading("28-error", error="CRC チェックが失敗しました"),
        ]

        for reading in cases:
            response = handle_api_request("/api/monitoring/compact", make_state([reading]))
            self.assertEqual(response.payload["level"], "unknown")
            self.assertEqual(response.payload["label"], "UNK")
            self.assertEqual(response.payload["issue_count"], 1)
            self.assertEqual(response.payload["tanks"][0]["status"], "unknown")

    def test_monitoring_compact_prefers_danger_over_warning(self) -> None:
        response = handle_api_request(
            "/api/monitoring/compact",
            make_state(
                [
                    make_reading("28-warning", temperature_c=29.2),
                    make_reading("28-danger", temperature_c=31.1),
                ]
            ),
        )

        self.assertEqual(response.payload["level"], "critical")
        self.assertEqual(response.payload["issue_count"], 2)
        self.assertEqual(response.payload["message"], "1 aquarium is outside the danger threshold.")

    def test_monitoring_compact_filters_non_visible_aquariums(self) -> None:
        response = handle_api_request(
            "/api/monitoring/compact",
            make_state(
                [
                    make_reading("28-outdoor", role="outdoor", sensor_type="air"),
                    make_reading("28-disabled", enabled=False),
                    make_reading("28-hidden", visible=False),
                    make_reading("28-aquarium", short_name="水槽", sort_order=10),
                ]
            ),
        )

        tanks = response.payload["tanks"]
        self.assertEqual([tank["sensor_id"] for tank in tanks], ["28-aquarium"])

    def test_monitoring_compact_allows_missing_short_name_ascii(self) -> None:
        response = handle_api_request(
            "/api/monitoring/compact",
            make_state([make_reading(short_name_ascii="", display_code="")]),
        )

        self.assertEqual(response.status, HTTPStatus.OK)
        self.assertEqual(response.payload["tanks"][0]["short_name_ascii"], "")
        self.assertEqual(response.payload["tanks"][0]["display_code"], "")

    def test_monitoring_compact_returns_empty_unknown_when_no_aquariums(self) -> None:
        response = handle_api_request(
            "/api/monitoring/compact",
            make_state([make_reading("28-outdoor", role="outdoor", sensor_type="air")]),
        )

        self.assertEqual(response.status, HTTPStatus.OK)
        self.assertEqual(response.payload["level"], "unknown")
        self.assertEqual(response.payload["label"], "UNK")
        self.assertFalse(response.payload["alert"])
        self.assertEqual(response.payload["issue_count"], 0)
        self.assertEqual(response.payload["title"], "No aquariums configured")
        self.assertEqual(response.payload["tanks"], [])

    def test_monitoring_compact_returns_500_for_provider_error(self) -> None:
        state = ApiState(
            config=AppConfig(sensors={}),
            readings_provider=lambda: (_ for _ in ()).throw(RuntimeError("sensor failure")),
        )

        response = handle_api_request("/api/monitoring/compact", state)

        self.assertEqual(response.status, HTTPStatus.INTERNAL_SERVER_ERROR)
        self.assertEqual(response.payload["error"]["code"], "monitoring_compact_failed")

    def test_sensor_detail_returns_matching_sensor(self) -> None:
        response = handle_api_request("/api/sensors/28-00000020f5ed", make_state([make_reading()]))

        self.assertEqual(response.status, HTTPStatus.OK)
        self.assertEqual(response.payload["sensor_id"], "28-00000020f5ed")
        self.assertEqual(response.payload["name"], "増田川水槽")
        self.assertEqual(response.payload["display_code"], "MDS")
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

    def test_environment_latest_returns_visible_sensors_and_latest_record(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            logging_config = make_logging_config(Path(tmp_dir))
            storage = SQLiteStorage(logging_config.database_path)
            now = datetime.now(timezone.utc)
            storage.insert_environment_readings([make_environment_reading()], now)

            response = handle_api_request(
                "/api/environment/latest",
                make_state(
                    [],
                    logging_config=logging_config,
                    environment_sensors=make_environment_sensors(),
                ),
            )

        self.assertEqual(response.status, HTTPStatus.OK)
        sensor = response.payload["sensors"][0]
        self.assertEqual(sensor["sensor_key"], "sht31_room")
        self.assertEqual(sensor["temperature_c"], 25.0)
        self.assertEqual(sensor["relative_humidity_percent"], 56.0)

    def test_environment_latest_can_include_hidden_definition_without_data(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            response = handle_api_request(
                "/api/environment/latest",
                make_state(
                    [],
                    logging_config=make_logging_config(Path(tmp_dir)),
                    environment_sensors=make_environment_sensors(visible=False),
                ),
            )
            included = handle_api_request(
                "/api/environment/latest",
                make_state(
                    [],
                    logging_config=make_logging_config(Path(tmp_dir)),
                    environment_sensors=make_environment_sensors(visible=False),
                ),
                query={"include_hidden": "true"},
            )

        self.assertEqual(response.status, HTTPStatus.OK)
        self.assertEqual(response.payload["sensors"], [])
        self.assertEqual(included.payload["sensors"][0]["sensor_key"], "sht31_room")
        self.assertIsNone(included.payload["sensors"][0]["temperature_c"])

    def test_environment_series_and_summary_return_history(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            logging_config = make_logging_config(Path(tmp_dir))
            storage = SQLiteStorage(logging_config.database_path)
            now = datetime.now(timezone.utc)
            storage.insert_environment_readings(
                [make_environment_reading(temperature_c=24.0, humidity_percent=55.0)],
                now - timedelta(minutes=2),
            )
            storage.insert_environment_readings(
                [make_environment_reading(temperature_c=26.0, humidity_percent=57.0)],
                now - timedelta(minutes=1),
            )
            state = make_state(
                [],
                logging_config=logging_config,
                environment_sensors=make_environment_sensors(),
            )

            series = handle_api_request(
                "/api/environment/series",
                state,
                query={"range": "24h", "sensor_key": "sht31_room"},
            )
            summary = handle_api_request(
                "/api/environment/summary",
                state,
                query={"range": "24h"},
            )

        self.assertEqual(series.status, HTTPStatus.OK)
        self.assertEqual(len(series.payload["points"]), 2)
        self.assertEqual(series.payload["points"][1]["relative_humidity_percent"], 57.0)
        self.assertEqual(summary.status, HTTPStatus.OK)
        self.assertEqual(summary.payload["sensors"][0]["temperature"]["avg_c"], 25.0)
        self.assertEqual(summary.payload["sensors"][0]["relative_humidity"]["current_percent"], 57.0)

    def test_summary_includes_environment_latest_when_available(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            logging_config = make_logging_config(Path(tmp_dir))
            SQLiteStorage(logging_config.database_path).insert_environment_readings(
                [make_environment_reading()],
                datetime.now(timezone.utc),
            )

            response = handle_api_request(
                "/api/summary",
                make_state(
                    [make_reading()],
                    logging_config=logging_config,
                    environment_sensors=make_environment_sensors(),
                ),
            )

        self.assertEqual(response.status, HTTPStatus.OK)
        self.assertEqual(response.payload["environment"]["temperature_c"], 25.0)
        self.assertEqual(response.payload["environment"]["relative_humidity_percent"], 56.0)

    def test_leak_latest_returns_dry_without_alert(self) -> None:
        response = handle_api_request(
            "/api/leak/latest",
            make_state([], leak_readings=[make_leak_reading(status="dry", raw_value=0)]),
        )

        self.assertEqual(response.status, HTTPStatus.OK)
        sensor = response.payload["sensors"][0]
        self.assertEqual(sensor["sensor_key"], "leak_main")
        self.assertEqual(sensor["status"], "dry")
        self.assertFalse(sensor["alert"])
        self.assertEqual(sensor["raw_value"], 0)

    def test_leak_latest_returns_wet_with_alert(self) -> None:
        response = handle_api_request(
            "/api/leak/latest",
            make_state([], leak_readings=[make_leak_reading(status="wet", raw_value=1)]),
        )

        self.assertEqual(response.status, HTTPStatus.OK)
        sensor = response.payload["sensors"][0]
        self.assertEqual(sensor["status"], "wet")
        self.assertTrue(sensor["alert"])

    def test_leak_latest_returns_unknown_definition_when_read_fails(self) -> None:
        response = handle_api_request(
            "/api/leak/latest",
            make_state([], leak_sensors=make_leak_sensors(), leak_provider_raises=True),
        )

        self.assertEqual(response.status, HTTPStatus.OK)
        sensor = response.payload["sensors"][0]
        self.assertEqual(sensor["sensor_key"], "leak_main")
        self.assertEqual(sensor["status"], "unknown")
        self.assertFalse(sensor["alert"])
        self.assertIsNone(sensor["measured_at"])

    def test_leak_latest_returns_empty_without_config(self) -> None:
        response = handle_api_request("/api/leak/latest", make_state([]))

        self.assertEqual(response.status, HTTPStatus.OK)
        self.assertEqual(response.payload["sensors"], [])

    def test_summary_includes_leak_status(self) -> None:
        response = handle_api_request(
            "/api/summary",
            make_state([make_reading()], leak_readings=[make_leak_reading(status="dry", raw_value=0)]),
        )

        self.assertEqual(response.status, HTTPStatus.OK)
        self.assertEqual(response.payload["leak"]["status"], "dry")
        self.assertFalse(response.payload["leak"]["alert"])

    def test_monitoring_compact_marks_leak_wet_as_critical(self) -> None:
        response = handle_api_request(
            "/api/monitoring/compact",
            make_state([make_reading()], leak_readings=[make_leak_reading(status="wet", raw_value=1)]),
        )

        self.assertEqual(response.status, HTTPStatus.OK)
        self.assertEqual(response.payload["level"], "critical")
        self.assertEqual(response.payload["label"], "DANGER")
        self.assertTrue(response.payload["alert"])
        self.assertEqual(response.payload["title"], "Leak detected")
        self.assertEqual(response.payload["leak"]["status"], "wet")
        self.assertTrue(response.payload["leak"]["alert"])


def make_state(
    readings: list[ConfiguredSensorReading],
    *,
    logging_config: LoggingConfig | None = None,
    weather_enabled: bool = False,
    sensors: dict[str, SensorConfig] | None = None,
    fans: dict[str, FanConfig] | None = None,
    fan_states: list[FanState] | None = None,
    fan_state_store: FanStateStore | None = None,
    environment_sensors: dict[str, EnvironmentSensorConfig] | None = None,
    leak_sensors: dict[str, "LeakSensorConfig"] | None = None,
    leak_readings: list[LeakReading] | None = None,
    leak_provider_raises: bool = False,
) -> ApiState:
    from aquapi.config import WeatherConfig

    def leak_provider(include_hidden: bool) -> list[LeakReading]:
        if leak_provider_raises:
            raise RuntimeError("gpio unavailable")
        return leak_readings or []

    return ApiState(
        config=AppConfig(
            sensors=sensors or {},
            fans=fans,
            environment_sensors=environment_sensors,
            leak_sensors=leak_sensors,
            logging=logging_config or LoggingConfig(),
            weather=WeatherConfig(enabled=weather_enabled),
        ),
        readings_provider=lambda: readings,
        leak_provider=leak_provider if leak_readings is not None or leak_provider_raises else None,
        fan_state_provider=(lambda: fan_states or []) if fan_states is not None else None,
        fan_state_store=fan_state_store,
    )


def make_logging_config(data_dir: Path) -> LoggingConfig:
    return LoggingConfig(
        enabled=True,
        interval_seconds=60,
        storage="sqlite",
        database_path=data_dir / "aquapi.sqlite3",
        retention_days=365,
    )


def make_fans(*, enabled: bool = True) -> dict[str, FanConfig]:
    return {
        "fan_1": FanConfig(
            fan_id="fan_1",
            name="Fan 1",
            gpio=22,
            active_high=True,
            enabled=enabled,
        )
    }


def make_bound_sensor_configs() -> dict[str, SensorConfig]:
    return {
        "28-00000020f5ed": SensorConfig(
            sensor_id="28-00000020f5ed",
            name="増田川水槽",
            type="water",
            offset=0.0,
            min=18.0,
            max=28.0,
            role="aquarium",
            enabled=True,
            visible=True,
            sort_order=10,
            short_name="増田川",
            short_name_ascii="MASUDA",
            display_code="MDS",
            fan_control=FanControlConfig(enabled=True, fan_id="fan_1", start_c=28.0, stop_c=27.5),
        )
    }


def make_fan_state(*, state: str, reason: str) -> FanState:
    return FanState(
        fan_id="fan_1",
        name="Fan 1",
        gpio=22,
        active_high=True,
        enabled=True,
        state=state,
        bound_tank_id="28-00000020f5ed",
        reason=reason,
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


def make_environment_sensors(
    *,
    visible: bool = True,
    enabled: bool = True,
) -> dict[str, EnvironmentSensorConfig]:
    return {
        "sht31_room": EnvironmentSensorConfig(
            sensor_key="sht31_room",
            name="室内",
            short_name="室内",
            short_name_ascii="ROOM",
            type="sht31",
            role="indoor",
            enabled=enabled,
            visible=visible,
            sort_order=200,
            i2c_bus=1,
            i2c_address=0x44,
            read_interval_seconds=60,
        )
    }


def make_environment_reading(
    *,
    temperature_c: float | None = 25.0,
    humidity_percent: float | None = 56.0,
    crc_ok: bool = True,
    error: str | None = None,
) -> EnvironmentReading:
    return EnvironmentReading(
        sensor_key="sht31_room",
        name="室内",
        short_name="室内",
        short_name_ascii="ROOM",
        type="sht31",
        role="indoor",
        enabled=True,
        visible=True,
        sort_order=200,
        temperature_c=temperature_c,
        relative_humidity_percent=humidity_percent,
        crc_ok=crc_ok,
        error=error,
    )


def make_leak_sensors() -> dict[str, "LeakSensorConfig"]:
    from aquapi.config import LeakSensorConfig

    return {
        "leak_main": LeakSensorConfig(
            sensor_key="leak_main",
            name="漏水センサー",
            short_name="漏水",
            short_name_ascii="LEAK",
            type="conductive_probe",
            role="leak",
            enabled=True,
            visible=True,
            sort_order=300,
            drive_gpio=17,
            sense_gpio=27,
            pull="down",
            active_state="high",
            read_interval_seconds=5,
            debounce_seconds=2,
        )
    }


def make_leak_reading(
    *,
    status: str,
    raw_value: int | None,
    error: str | None = None,
) -> LeakReading:
    return LeakReading(
        sensor_key="leak_main",
        name="漏水センサー",
        short_name="漏水",
        short_name_ascii="LEAK",
        type="conductive_probe",
        role="leak",
        enabled=True,
        visible=True,
        sort_order=300,
        status=status,
        alert=status == "wet",
        raw_value=raw_value,
        measured_at=datetime(2026, 6, 9, 18, 0, tzinfo=timezone.utc),
        error=error,
    )


if __name__ == "__main__":
    unittest.main()
