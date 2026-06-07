from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from aquapi.config import AppConfig, SensorConfig
from aquapi.logs import RANGE_DELTAS, build_history_summary_payload, build_series_payload
from aquapi.sensors import (
    ConfiguredSensorReading,
    configured_sensor_reading_to_dict,
    read_all_configured_sensors,
)
from aquapi.sqlite_storage import SQLiteStorage


ReadingsProvider = Callable[[], list[ConfiguredSensorReading]]
STATUS_KEYS = ("ok", "low", "high", "unknown", "error")
COMPACT_STATUS_PRIORITY = {
    "danger": 3,
    "warning": 2,
    "unknown": 1,
    "safety": 0,
}


@dataclass(frozen=True)
class ApiState:
    config: AppConfig
    readings_provider: ReadingsProvider
    version: str = "dev"


@dataclass(frozen=True)
class ApiResponse:
    status: HTTPStatus
    payload: dict[str, object]


def create_readings_provider(config: AppConfig) -> ReadingsProvider:
    return lambda: read_all_configured_sensors(config=config)


def create_handler(state: ApiState) -> type[BaseHTTPRequestHandler]:
    class AquapiRequestHandler(BaseHTTPRequestHandler):
        server_version = "aquapi/dev"

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            response = handle_api_request(
                parsed.path,
                state,
                query={key: values[0] for key, values in parse_qs(parsed.query).items()},
            )
            self._send_json(response.status, response.payload)

        def log_message(self, format: str, *args: object) -> None:
            return

        def _send_json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return AquapiRequestHandler


def handle_api_request(
    path: str,
    state: ApiState,
    query: dict[str, str] | None = None,
) -> ApiResponse:
    params = query or {}

    if path == "/api/health":
        return ApiResponse(
            HTTPStatus.OK,
            {"ok": True, "service": "aquapi", "version": state.version},
        )

    if path == "/api/readings/series":
        return _handle_series(state, params)

    if path == "/api/readings/summary":
        return _handle_history_summary(state, params)

    if path == "/api/weather/latest":
        return _handle_weather_latest(state)

    if path == "/api/weather/series":
        return _handle_weather_series(state, params)

    if path == "/api/weather/summary":
        return _handle_weather_summary(state, params)

    if path == "/api/monitoring/compact":
        return _handle_monitoring_compact(state)

    if path == "/api/sensors":
        return ApiResponse(HTTPStatus.OK, build_sensors_payload(state.config))

    if path == "/api/readings":
        return ApiResponse(HTTPStatus.OK, build_readings_payload(state.readings_provider()))

    if path == "/api/summary":
        return ApiResponse(HTTPStatus.OK, build_summary_payload(state.readings_provider()))

    if path.startswith("/api/sensors/"):
        sensor_id = unquote(path.removeprefix("/api/sensors/"))
        reading = find_reading(state.readings_provider(), sensor_id)
        if reading is None:
            return ApiResponse(
                HTTPStatus.NOT_FOUND,
                {
                    "error": {
                        "code": "sensor_not_found",
                        "message": "sensor not found",
                        "sensor_id": sensor_id,
                    }
                },
            )

        return ApiResponse(HTTPStatus.OK, build_sensor_detail_payload(reading))

    return ApiResponse(
        HTTPStatus.NOT_FOUND,
        {
            "error": {
                "code": "not_found",
                "message": "not found",
            }
        },
    )


def _handle_series(state: ApiState, params: dict[str, str]) -> ApiResponse:
    range_text = params.get("range", "24h")
    sensor_id = params.get("sensor_id")
    name = params.get("name")
    if sensor_id is None and name is None:
        return ApiResponse(
            HTTPStatus.BAD_REQUEST,
            {
                "error": {
                    "code": "missing_sensor",
                    "message": "sensor_id or name is required",
                }
            },
        )

    try:
        payload = build_series_payload(
            state.config.logging,
            range_text=range_text,
            sensor_id=sensor_id,
            name=name,
        )
    except ValueError as exc:
        return _range_error(exc)

    if payload is None:
        return ApiResponse(
            HTTPStatus.NOT_FOUND,
            {
                "error": {
                    "code": "sensor_not_found",
                    "message": "sensor history not found",
                    "sensor_id": sensor_id,
                    "name": name,
                }
            },
        )

    return ApiResponse(HTTPStatus.OK, payload)


def _handle_history_summary(state: ApiState, params: dict[str, str]) -> ApiResponse:
    range_text = params.get("range", "24h")
    try:
        return ApiResponse(
            HTTPStatus.OK,
            build_history_summary_payload(state.config.logging, range_text=range_text),
        )
    except ValueError as exc:
        return _range_error(exc)


def _range_error(exc: ValueError) -> ApiResponse:
    return ApiResponse(
        HTTPStatus.BAD_REQUEST,
        {
            "error": {
                "code": "invalid_range",
                "message": str(exc),
            }
        },
    )


def _handle_monitoring_compact(state: ApiState) -> ApiResponse:
    try:
        readings = state.readings_provider()
        payload = build_monitoring_compact_payload(readings)
    except Exception as exc:
        return ApiResponse(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            {
                "error": {
                    "code": "monitoring_compact_failed",
                    "message": str(exc),
                }
            },
        )

    return ApiResponse(HTTPStatus.OK, payload)


def _handle_weather_latest(state: ApiState) -> ApiResponse:
    disabled = _weather_disabled_response(state)
    if disabled is not None:
        return disabled

    payload = SQLiteStorage(state.config.logging.database_path).get_latest_weather()
    if payload is None:
        return _weather_not_found_response()

    return ApiResponse(
        HTTPStatus.OK,
        {
            "generated_at": _now_iso(),
            "weather": payload,
        },
    )


def _handle_weather_series(state: ApiState, params: dict[str, str]) -> ApiResponse:
    disabled = _weather_disabled_response(state)
    if disabled is not None:
        return disabled

    range_text = params.get("range", "24h")
    try:
        start, end = _range_bounds(range_text)
    except ValueError as exc:
        return _range_error(exc)

    points = SQLiteStorage(state.config.logging.database_path).get_weather_series(
        start=start,
        end=end,
    )
    return ApiResponse(
        HTTPStatus.OK,
        {
            "range": range_text,
            "points": points,
        },
    )


def _handle_weather_summary(state: ApiState, params: dict[str, str]) -> ApiResponse:
    disabled = _weather_disabled_response(state)
    if disabled is not None:
        return disabled

    range_text = params.get("range", "7d")
    try:
        start, end = _range_bounds(range_text)
    except ValueError as exc:
        return _range_error(exc)

    return ApiResponse(
        HTTPStatus.OK,
        SQLiteStorage(state.config.logging.database_path).get_weather_summary(
            range_text=range_text,
            start=start,
            end=end,
        ),
    )


def _weather_disabled_response(state: ApiState) -> ApiResponse | None:
    if state.config.weather.enabled:
        return None
    return ApiResponse(
        HTTPStatus.SERVICE_UNAVAILABLE,
        {
            "error": {
                "code": "weather_disabled",
                "message": "weather integration is disabled",
            }
        },
    )


def _weather_not_found_response() -> ApiResponse:
    return ApiResponse(
        HTTPStatus.NOT_FOUND,
        {
            "error": {
                "code": "weather_not_found",
                "message": "weather data not found",
            }
        },
    )


def _range_bounds(range_text: str) -> tuple[datetime, datetime]:
    delta = RANGE_DELTAS.get(range_text)
    if delta is None:
        raise ValueError(f"unsupported range: {range_text}")
    end = datetime.now().astimezone()
    return end - delta, end


def build_readings_payload(readings: list[ConfiguredSensorReading]) -> dict[str, object]:
    return {
        "generated_at": _now_iso(),
        "sensors": [
            configured_sensor_reading_to_dict(reading)
            for reading in _sort_readings(_visible_readings(readings))
        ],
    }


def build_summary_payload(readings: list[ConfiguredSensorReading]) -> dict[str, object]:
    counts = {status: 0 for status in STATUS_KEYS}
    for reading in _visible_readings(readings):
        if reading.status in counts:
            counts[reading.status] += 1
        else:
            counts["unknown"] += 1

    status = _overall_status(counts)
    alert = status in {"low", "high", "error"}

    return {
        "generated_at": _now_iso(),
        "status": status,
        "counts": counts,
        "alert": alert,
        "message": "all sensors ok" if status == "ok" else f"{status} sensors found",
    }


def build_sensor_detail_payload(reading: ConfiguredSensorReading) -> dict[str, object]:
    return {
        "sensor_id": reading.sensor_id,
        "name": reading.name,
        "type": reading.type,
        "role": reading.role,
        "enabled": reading.enabled,
        "visible": reading.visible,
        "sort_order": reading.sort_order,
        "temperature_c": reading.temperature_c,
        "status": reading.status,
    }


def build_sensors_payload(config: AppConfig) -> dict[str, object]:
    return {
        "sensors": [_sensor_config_to_dict(sensor_config) for sensor_config in _sort_sensor_configs(config)],
    }


def build_monitoring_compact_payload(readings: list[ConfiguredSensorReading]) -> dict[str, object]:
    tanks = [_compact_tank(reading) for reading in _sort_readings(_compact_aquarium_readings(readings))]
    if not tanks:
        return {
            "source": "aquapi",
            "generated_at": _now_iso(),
            "level": "unknown",
            "label": "UNK",
            "alert": False,
            "title": "No aquariums configured",
            "message": "No visible aquarium sensors are configured.",
            "issue_count": 0,
            "tanks": [],
        }

    level = _compact_level([str(tank["status"]) for tank in tanks])
    issue_count = sum(1 for tank in tanks if tank["status"] in {"warning", "danger", "unknown"})
    title, message = _compact_title_message(level, _compact_message_count(level, tanks))
    return {
        "source": "aquapi",
        "generated_at": _now_iso(),
        "level": level,
        "label": _compact_label(level),
        "alert": level != "ok",
        "title": title,
        "message": message,
        "issue_count": issue_count,
        "tanks": tanks,
    }


def find_reading(
    readings: list[ConfiguredSensorReading],
    sensor_id: str,
) -> ConfiguredSensorReading | None:
    for reading in readings:
        if reading.sensor_id == sensor_id:
            return reading
    return None


def _compact_aquarium_readings(readings: list[ConfiguredSensorReading]) -> list[ConfiguredSensorReading]:
    return [
        reading
        for reading in readings
        if reading.role == "aquarium" and reading.enabled and reading.visible
    ]


def _compact_tank(reading: ConfiguredSensorReading) -> dict[str, object]:
    status = _compact_status(reading)
    return {
        "sensor_id": reading.sensor_id,
        "name": reading.name,
        "short_name": reading.short_name,
        "temperature_c": reading.temperature_c,
        "status": status,
        "alert": status in {"warning", "danger", "unknown"},
    }


def _compact_status(reading: ConfiguredSensorReading) -> str:
    if (
        reading.temperature_c is None
        or reading.min is None
        or reading.max is None
        or not reading.crc_ok
        or reading.error is not None
        or reading.min > reading.max
    ):
        return "unknown"

    temperature = reading.temperature_c
    if reading.min <= temperature <= reading.max:
        return "safety"
    if reading.min - 2.0 <= temperature < reading.min:
        return "warning"
    if reading.max < temperature <= reading.max + 2.0:
        return "warning"
    return "danger"


def _compact_level(statuses: list[str]) -> str:
    highest = max(statuses, key=lambda status: COMPACT_STATUS_PRIORITY.get(status, 0))
    if highest == "danger":
        return "critical"
    if highest == "warning":
        return "warning"
    if highest == "unknown":
        return "unknown"
    return "ok"


def _compact_label(level: str) -> str:
    return {
        "ok": "AQUA OK",
        "warning": "WARN",
        "critical": "DANGER",
        "unknown": "UNK",
    }[level]


def _compact_title_message(level: str, issue_count: int) -> tuple[str, str]:
    if level == "ok":
        return (
            "All aquariums are safe",
            "All aquarium temperatures are within safety range.",
        )
    if level == "warning":
        return (
            "Aquarium temperature warning",
            _issue_message(issue_count, "outside the safety range"),
        )
    if level == "critical":
        return (
            "Dangerous aquarium temperature",
            _issue_message(issue_count, "outside the danger threshold"),
        )
    return (
        "Aquarium status unavailable",
        "AquaPi cannot determine current aquarium temperature status.",
    )


def _compact_message_count(level: str, tanks: list[dict[str, object]]) -> int:
    if level == "critical":
        return sum(1 for tank in tanks if tank["status"] == "danger")
    if level == "warning":
        return sum(1 for tank in tanks if tank["status"] == "warning")
    return sum(1 for tank in tanks if tank["status"] == "unknown")


def _issue_message(issue_count: int, suffix: str) -> str:
    if issue_count == 1:
        return f"1 aquarium is {suffix}."
    return f"{issue_count} aquariums are {suffix}."


def _visible_readings(readings: list[ConfiguredSensorReading]) -> list[ConfiguredSensorReading]:
    return [reading for reading in readings if reading.enabled and reading.visible]


def _sort_readings(readings: list[ConfiguredSensorReading]) -> list[ConfiguredSensorReading]:
    return sorted(readings, key=lambda reading: (reading.sort_order, reading.name, reading.sensor_id))


def _sort_sensor_configs(config: AppConfig) -> list[SensorConfig]:
    return sorted(
        config.sensors.values(),
        key=lambda sensor_config: (sensor_config.sort_order, sensor_config.name, sensor_config.sensor_id),
    )


def _sensor_config_to_dict(sensor_config: SensorConfig) -> dict[str, object]:
    return {
        "sensor_id": sensor_config.sensor_id,
        "name": sensor_config.name,
        "type": sensor_config.type,
        "role": sensor_config.role,
        "enabled": sensor_config.enabled,
        "visible": sensor_config.visible,
        "sort_order": sensor_config.sort_order,
        "short_name": sensor_config.short_name,
        "min": sensor_config.min,
        "max": sensor_config.max,
        "offset": sensor_config.offset,
    }


def serve_api(
    config: AppConfig,
    *,
    host: str | None = None,
    port: int | None = None,
    readings_provider: ReadingsProvider | None = None,
) -> None:
    listen_host = host if host is not None else config.listen_addr
    listen_port = port if port is not None else config.listen_port
    state = ApiState(
        config=config,
        readings_provider=readings_provider or create_readings_provider(config),
    )
    server = ThreadingHTTPServer((listen_host, listen_port), create_handler(state))
    try:
        server.serve_forever()
    finally:
        server.server_close()


def _overall_status(counts: dict[str, int]) -> str:
    if counts["error"] > 0:
        return "error"
    if counts["high"] > 0:
        return "high"
    if counts["low"] > 0:
        return "low"
    if counts["unknown"] > 0:
        return "unknown"
    return "ok"


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
