from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

from aquapi.config import AppConfig
from aquapi.sensors import (
    ConfiguredSensorReading,
    configured_sensor_reading_to_dict,
    read_all_configured_sensors,
)


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
            response = handle_api_request(urlparse(self.path).path, state)
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


def handle_api_request(path: str, state: ApiState) -> ApiResponse:
    if path == "/api/health":
        return ApiResponse(
            HTTPStatus.OK,
            {"ok": True, "service": "aquapi", "version": state.version},
        )

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
