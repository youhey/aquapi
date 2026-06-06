from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from aquapi.config import AppConfig
from aquapi.logs import RANGE_DELTAS, build_history_summary_payload, build_series_payload
from aquapi.sensors import (
    ConfiguredSensorReading,
    configured_sensor_reading_to_dict,
    read_all_configured_sensors,
)
from aquapi.sqlite_storage import SQLiteStorage


ReadingsProvider = Callable[[], list[ConfiguredSensorReading]]
STATUS_KEYS = ("ok", "low", "high", "unknown", "error")


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
        "sensors": [configured_sensor_reading_to_dict(reading) for reading in readings],
    }


def build_summary_payload(readings: list[ConfiguredSensorReading]) -> dict[str, object]:
    counts = {status: 0 for status in STATUS_KEYS}
    for reading in readings:
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
        "temperature_c": reading.temperature_c,
        "status": reading.status,
    }


def find_reading(
    readings: list[ConfiguredSensorReading],
    sensor_id: str,
) -> ConfiguredSensorReading | None:
    for reading in readings:
        if reading.sensor_id == sensor_id:
            return reading
    return None


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
