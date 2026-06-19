from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from aquapi.config import AppConfig, SensorConfig
from aquapi.fans import (
    FAN_MODE_AUTO,
    FAN_MODE_MANUAL_OFF,
    FAN_MODE_MANUAL_ON,
    FanState,
    FanStateStore,
    FanModeError,
    build_fan_states,
    compact_fan_payload,
    default_fan_state_path,
    fan_control_payload,
    fan_state_to_dict,
    fan_states_by_id,
    set_fan_mode,
    temperature_alert_payload,
    temperature_alert_state,
)
from aquapi.leak import LeakReading, leak_reading_to_dict, read_all_leak_sensors, unknown_leak_readings
from aquapi.logs import RANGE_DELTAS, build_history_summary_payload, build_series_payload
from aquapi.sensors import (
    ConfiguredSensorReading,
    configured_sensor_reading_to_dict,
    read_all_configured_sensors,
)
from aquapi.sqlite_storage import SQLiteStorage


ReadingsProvider = Callable[[], list[ConfiguredSensorReading]]
LeakProvider = Callable[[bool], list[LeakReading]]
FanStateProvider = Callable[[], list[FanState]]
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
    leak_provider: LeakProvider | None = None
    fan_state_provider: FanStateProvider | None = None
    fan_state_store: FanStateStore | None = None


@dataclass(frozen=True)
class ApiResponse:
    status: HTTPStatus
    payload: dict[str, object]


def create_readings_provider(config: AppConfig) -> ReadingsProvider:
    return lambda: read_all_configured_sensors(config=config)


def create_fan_state_provider(config: AppConfig) -> FanStateProvider:
    store = FanStateStore(default_fan_state_path(config))
    return lambda: list(store.load(config).values())


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

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            response = handle_api_request(
                parsed.path,
                state,
                query={key: values[0] for key, values in parse_qs(parsed.query).items()},
                method="POST",
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
    method: str = "GET",
) -> ApiResponse:
    params = query or {}

    if method == "POST":
        return _handle_post_request(path, state)
    if method != "GET":
        return ApiResponse(
            HTTPStatus.METHOD_NOT_ALLOWED,
            {"error": {"code": "method_not_allowed", "message": "method not allowed"}},
        )

    if path == "/api/health":
        return ApiResponse(
            HTTPStatus.OK,
            {"ok": True, "service": "aquapi", "version": state.version},
        )

    if path == "/api/readings/series":
        return _handle_series(state, params)

    if path == "/api/readings/summary":
        return _handle_history_summary(state, params)

    if path == "/api/environment/latest":
        return _handle_environment_latest(state, params)

    if path == "/api/environment/series":
        return _handle_environment_series(state, params)

    if path == "/api/environment/summary":
        return _handle_environment_summary(state, params)

    if path == "/api/leak/latest":
        return _handle_leak_latest(state, params)

    if path == "/api/tanks/latest":
        return _handle_tanks_latest(state, params)

    if path == "/api/fans":
        return _handle_fans(state)

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
        return _handle_current_summary(state)

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


def _handle_post_request(path: str, state: ApiState) -> ApiResponse:
    fan_prefix = "/api/fans/"
    if path.startswith(fan_prefix):
        parts = path.removeprefix(fan_prefix).split("/")
        if len(parts) == 2:
            fan_id = unquote(parts[0])
            action = parts[1]
            mode_by_action = {
                "manual-on": FAN_MODE_MANUAL_ON,
                "manual-off": FAN_MODE_MANUAL_OFF,
                "auto": FAN_MODE_AUTO,
            }
            mode = mode_by_action.get(action)
            if mode is not None:
                return _handle_fan_mode_change(state, fan_id, mode, event_reason=f"api_{action.replace('-', '_')}")

    return ApiResponse(
        HTTPStatus.NOT_FOUND,
        {"error": {"code": "not_found", "message": "not found"}},
    )


def _handle_fans(state: ApiState) -> ApiResponse:
    readings = state.readings_provider()
    fan_states = _fan_states_for_api(state, readings)
    return ApiResponse(
        HTTPStatus.OK,
        {
            "generated_at": _now_iso(),
            "fans": [_fan_api_payload(state.config, fan_state) for fan_state in fan_states],
        },
    )


def _handle_fan_mode_change(state: ApiState, fan_id: str, mode: str, *, event_reason: str) -> ApiResponse:
    try:
        readings = state.readings_provider()
        result = set_fan_mode(
            state.config,
            fan_id,
            mode,
            readings,
            state_store=_fan_state_store(state),
            event_reason=event_reason,
        )
    except FanModeError as exc:
        status = HTTPStatus.NOT_FOUND if exc.code == "fan_not_found" else HTTPStatus.CONFLICT
        if exc.code == "invalid_fan_mode":
            status = HTTPStatus.BAD_REQUEST
        return ApiResponse(status, {"error": exc.code, "message": exc.message})

    return ApiResponse(
        HTTPStatus.OK,
        {
            "generated_at": _now_iso(),
            "fan": _fan_api_payload(state.config, result.current),
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


def _handle_current_summary(state: ApiState) -> ApiResponse:
    readings = state.readings_provider()
    fan_states = _fan_states_for_api(state, readings)
    payload = build_summary_payload(readings, config=state.config, fan_states=fan_states)
    if state.config.logging.storage == "sqlite" and state.config.configured_environment_sensors():
        try:
            latest = SQLiteStorage(state.config.logging.database_path).get_environment_latest(state.config)
        except (OSError, sqlite3.Error):
            latest = None
        if latest is not None:
            sensors = latest.get("sensors", [])
            if isinstance(sensors, list) and sensors:
                first = sensors[0]
                if isinstance(first, dict):
                    payload["environment"] = {
                        "temperature_c": first.get("temperature_c"),
                        "relative_humidity_percent": first.get("relative_humidity_percent"),
                        "measured_at": first.get("measured_at"),
                    }
    leak_readings = _leak_readings(state)
    if leak_readings:
        payload["leak"] = _summary_leak_payload(leak_readings)
    return ApiResponse(HTTPStatus.OK, payload)


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
        payload = build_monitoring_compact_payload(
            readings,
            leak_readings=_leak_readings(state),
            config=state.config,
            fan_states=_fan_states_for_api(state, readings),
        )
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


def _handle_environment_latest(state: ApiState, params: dict[str, str]) -> ApiResponse:
    include_hidden = params.get("include_hidden") == "true"
    payload = SQLiteStorage(state.config.logging.database_path).get_environment_latest(
        state.config,
        include_hidden=include_hidden,
    )
    return ApiResponse(
        HTTPStatus.OK,
        {
            "generated_at": _now_iso(),
            **payload,
        },
    )


def _handle_environment_series(state: ApiState, params: dict[str, str]) -> ApiResponse:
    range_text = params.get("range", "24h")
    try:
        start, end = _range_bounds(range_text)
    except ValueError as exc:
        return _range_error(exc)
    payload = SQLiteStorage(state.config.logging.database_path).get_environment_series(
        state.config,
        range_text=range_text,
        start=start,
        end=end,
        sensor_key=params.get("sensor_key"),
    )
    return ApiResponse(HTTPStatus.OK, {"generated_at": _now_iso(), **payload})


def _handle_environment_summary(state: ApiState, params: dict[str, str]) -> ApiResponse:
    range_text = params.get("range", "24h")
    try:
        start, end = _range_bounds(range_text)
    except ValueError as exc:
        return _range_error(exc)
    payload = SQLiteStorage(state.config.logging.database_path).get_environment_summary(
        state.config,
        range_text=range_text,
        start=start,
        end=end,
    )
    return ApiResponse(HTTPStatus.OK, {"generated_at": _now_iso(), **payload})


def _handle_leak_latest(state: ApiState, params: dict[str, str]) -> ApiResponse:
    include_hidden = params.get("include_hidden") == "true"
    readings = _leak_readings(state, include_hidden=include_hidden)
    return ApiResponse(
        HTTPStatus.OK,
        {
            "generated_at": _now_iso(),
            "sensors": [leak_reading_to_dict(reading) for reading in readings],
        },
    )


def _handle_tanks_latest(state: ApiState, params: dict[str, str]) -> ApiResponse:
    include_hidden = params.get("include_hidden") == "true"
    readings = state.readings_provider()
    fan_states = _fan_states_for_api(state, readings)
    tanks = [
        _compact_tank(reading, fan_states=fan_states_by_id(fan_states))
        for reading in _sort_readings(_tank_readings(readings, include_hidden))
    ]
    return ApiResponse(
        HTTPStatus.OK,
        {
            "generated_at": _now_iso(),
            "tanks": tanks,
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
        "sensors": [
            configured_sensor_reading_to_dict(reading)
            for reading in _sort_readings(_visible_readings(readings))
        ],
    }


def build_summary_payload(
    readings: list[ConfiguredSensorReading],
    *,
    config: AppConfig | None = None,
    fan_states: list[FanState] | None = None,
) -> dict[str, object]:
    counts = {status: 0 for status in STATUS_KEYS}
    for reading in _visible_readings(readings):
        if reading.status in counts:
            counts[reading.status] += 1
        else:
            counts["unknown"] += 1

    status = _overall_status(counts)
    alert = status in {"low", "high", "error"}

    payload: dict[str, object] = {
        "generated_at": _now_iso(),
        "status": status,
        "counts": counts,
        "alert": alert,
        "message": "all sensors ok" if status == "ok" else f"{status} sensors found",
    }
    if config is not None:
        fan_state_map = fan_states_by_id(fan_states or [])
        payload["tanks"] = [
            _summary_tank(reading, fan_state_map)
            for reading in _sort_readings(_tank_readings(readings, include_hidden=False))
        ]
        payload["fans"] = [fan_state_to_dict(state) for state in fan_states or []]
    return payload


def build_sensor_detail_payload(reading: ConfiguredSensorReading) -> dict[str, object]:
    return {
        "sensor_id": reading.sensor_id,
        "name": reading.name,
        "type": reading.type,
        "role": reading.role,
        "enabled": reading.enabled,
        "visible": reading.visible,
        "sort_order": reading.sort_order,
        "short_name": reading.short_name,
        "short_name_ascii": reading.short_name_ascii,
        "display_code": reading.display_code,
        "temperature_alert": temperature_alert_payload(reading),
        "fan_control": fan_control_payload(reading, {}),
        "temperature_c": reading.temperature_c,
        "status": reading.status,
    }


def build_sensors_payload(config: AppConfig) -> dict[str, object]:
    return {
        "sensors": [_sensor_config_to_dict(sensor_config) for sensor_config in _sort_sensor_configs(config)],
    }


def build_monitoring_compact_payload(
    readings: list[ConfiguredSensorReading],
    *,
    leak_readings: list[LeakReading] | None = None,
    config: AppConfig | None = None,
    fan_states: list[FanState] | None = None,
) -> dict[str, object]:
    fan_state_map = fan_states_by_id(fan_states or [])
    tanks = [
        _compact_tank(reading, fan_states=fan_state_map)
        for reading in _sort_readings(_compact_aquarium_readings(readings))
    ]
    leak = _compact_leak_payload(leak_readings or [])
    leak_alert_count = sum(1 for reading in leak_readings or [] if reading.status == "wet")
    leak_unknown_count = sum(1 for reading in leak_readings or [] if reading.status == "unknown")
    if leak_alert_count > 0:
        return {
            "source": "aquapi",
            "generated_at": _now_iso(),
            "level": "critical",
            "label": "DANGER",
            "alert": True,
            "title": "Leak detected",
            "message": "Leak sensor detected water.",
            "issue_count": leak_alert_count,
            "tanks": tanks,
            "fans": [compact_fan_payload(state) for state in fan_states or []],
            "leak": leak,
        }

    if not tanks:
        if leak["sensors"]:
            level = "unknown" if leak_unknown_count > 0 else "ok"
            title, message = (
                ("Leak sensor status unavailable", "AquaPi cannot determine current leak sensor status.")
                if level == "unknown"
                else ("All aquariums are safe", "All aquarium temperatures are within safety range.")
            )
            return {
                "source": "aquapi",
                "generated_at": _now_iso(),
                "level": level,
                "label": _compact_label(level),
                "alert": level != "ok",
                "title": title,
                "message": message,
                "issue_count": leak_unknown_count,
                "tanks": [],
                "fans": [compact_fan_payload(state) for state in fan_states or []],
                "leak": leak,
            }
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
            "fans": [compact_fan_payload(state) for state in fan_states or []],
            "leak": leak,
        }

    level = _compact_level([str(tank["status"]) for tank in tanks])
    if level == "ok" and leak_unknown_count > 0:
        level = "unknown"
    issue_count = sum(1 for tank in tanks if tank["status"] in {"warning", "danger", "unknown"})
    if level == "unknown":
        issue_count += leak_unknown_count
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
        "fans": [compact_fan_payload(state) for state in fan_states or []],
        "leak": leak,
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
    return _tank_readings(readings, include_hidden=False)


def _tank_readings(
    readings: list[ConfiguredSensorReading],
    include_hidden: bool,
) -> list[ConfiguredSensorReading]:
    return [
        reading
        for reading in readings
        if reading.role == "aquarium" and reading.enabled and (include_hidden or reading.visible)
    ]


def _summary_tank(reading: ConfiguredSensorReading, fan_states: dict[str, FanState]) -> dict[str, object]:
    fan_state = fan_states.get(reading.fan_control.fan_id)
    return {
        "sensor_id": reading.sensor_id,
        "name": reading.name,
        "short_name": reading.short_name,
        "short_name_ascii": reading.short_name_ascii,
        "display_code": reading.display_code,
        "temperature_c": reading.temperature_c,
        "status": reading.status,
        "temperature_alert": temperature_alert_payload(reading),
        "fan_control": fan_control_payload(reading, fan_states),
        "fan_id": reading.fan_control.fan_id,
        "fan_state": fan_state.state if fan_state is not None else ("disabled" if not reading.fan_control.enabled else "unknown"),
        "fan_mode": fan_state.mode if fan_state is not None else FAN_MODE_AUTO,
        "fan_reason": fan_state.reason if fan_state is not None else _fan_default_reason(reading),
    }


def _compact_tank(
    reading: ConfiguredSensorReading,
    *,
    fan_states: dict[str, FanState] | None = None,
) -> dict[str, object]:
    status = _compact_status(reading)
    fan_state_map = fan_states or {}
    fan_state = fan_state_map.get(reading.fan_control.fan_id)
    return {
        "sensor_id": reading.sensor_id,
        "name": reading.name,
        "short_name": reading.short_name,
        "short_name_ascii": reading.short_name_ascii,
        "display_code": reading.display_code,
        "temperature_c": reading.temperature_c,
        "status": status,
        "temperature_alert_state": temperature_alert_state(reading),
        "fan_id": reading.fan_control.fan_id,
        "fan_state": fan_state.state if fan_state is not None else ("disabled" if not reading.fan_control.enabled else "unknown"),
        "fan_mode": fan_state.mode if fan_state is not None else FAN_MODE_AUTO,
        "fan_reason": fan_state.reason if fan_state is not None else _fan_default_reason(reading),
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


def _leak_readings(state: ApiState, *, include_hidden: bool = False) -> list[LeakReading]:
    try:
        if state.leak_provider is not None:
            return state.leak_provider(include_hidden)
        return read_all_leak_sensors(state.config, include_hidden=include_hidden)
    except Exception:
        return unknown_leak_readings(state.config, include_hidden=include_hidden)


def _summary_leak_payload(readings: list[LeakReading]) -> dict[str, object]:
    return {
        "status": _leak_overall_status(readings),
        "alert": any(reading.alert for reading in readings),
        "sensors": [
            {
                "sensor_key": reading.sensor_key,
                "status": reading.status,
                "alert": reading.alert,
                "measured_at": _datetime_to_iso(reading.measured_at),
            }
            for reading in readings
        ],
    }


def _compact_leak_payload(readings: list[LeakReading]) -> dict[str, object]:
    status = _leak_overall_status(readings)
    return {
        "status": status,
        "alert": status == "wet",
        "label": _compact_leak_label(status),
        "sensors": [
            {
                "sensor_key": reading.sensor_key,
                "short_name": reading.short_name,
                "short_name_ascii": reading.short_name_ascii,
                "status": reading.status,
                "alert": reading.alert,
                "measured_at": _datetime_to_iso(reading.measured_at),
            }
            for reading in readings
        ],
    }


def _leak_overall_status(readings: list[LeakReading]) -> str:
    if any(reading.status == "wet" for reading in readings):
        return "wet"
    if any(reading.status == "unknown" for reading in readings) or not readings:
        return "unknown"
    return "dry"


def _compact_leak_label(status: str) -> str:
    if status == "wet":
        return "LEAK"
    if status == "dry":
        return "LEAK OK"
    return "LEAK UNK"


def _datetime_to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone().isoformat(timespec="seconds")


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
        "short_name_ascii": sensor_config.short_name_ascii,
        "display_code": sensor_config.display_code,
        "temperature_alert": {
            "enabled": sensor_config.temperature_alert.enabled,
            "too_hot_c": sensor_config.temperature_alert.too_hot_c,
            "too_cold_c": sensor_config.temperature_alert.too_cold_c,
        },
        "fan_control": {
            "enabled": sensor_config.fan_control.enabled,
            "fan_id": sensor_config.fan_control.fan_id,
            "start_c": sensor_config.fan_control.start_c,
            "stop_c": sensor_config.fan_control.stop_c,
        },
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
        fan_state_provider=create_fan_state_provider(config),
        fan_state_store=FanStateStore(default_fan_state_path(config)),
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


def _fan_states_for_api(state: ApiState, readings: list[ConfiguredSensorReading]) -> list[FanState]:
    if not state.config.configured_fans():
        return []
    previous_states: dict[str, FanState] = {}
    if state.fan_state_provider is not None:
        previous_states = fan_states_by_id(state.fan_state_provider())
    else:
        previous_states = _fan_state_store(state).load(state.config)
    return build_fan_states(state.config, readings, previous_states=previous_states)


def _fan_state_store(state: ApiState) -> FanStateStore:
    return state.fan_state_store or FanStateStore(default_fan_state_path(state.config))


def _fan_api_payload(config: AppConfig, fan_state: FanState) -> dict[str, object]:
    payload = fan_state_to_dict(fan_state)
    sensor_config = _bound_sensor_config(config, fan_state.bound_tank_id)
    payload["bound_tank_name"] = sensor_config.name if sensor_config is not None else None
    payload["bound_tank_display_code"] = sensor_config.display_code if sensor_config is not None else None
    return payload


def _bound_sensor_config(config: AppConfig, sensor_id: str | None) -> SensorConfig | None:
    if sensor_id is None:
        return None
    return config.sensors.get(sensor_id)


def _fan_default_reason(reading: ConfiguredSensorReading) -> str:
    if not reading.fan_control.enabled:
        return "tank_fan_control_disabled"
    return "startup_off"
