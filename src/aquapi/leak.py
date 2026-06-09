from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime

from aquapi.config import AppConfig, LeakSensorConfig


LEAK_STATUS_DRY = "dry"
LEAK_STATUS_WET = "wet"
LEAK_STATUS_UNKNOWN = "unknown"


@dataclass(frozen=True)
class LeakReading:
    sensor_key: str
    name: str
    short_name: str
    short_name_ascii: str
    type: str
    role: str
    enabled: bool
    visible: bool
    sort_order: int
    status: str
    alert: bool
    raw_value: int | None
    measured_at: datetime | None
    error: str | None = None


@dataclass
class _LeakRuntimeState:
    latest: LeakReading | None = None
    stable_status: str | None = None
    candidate_status: str | None = None
    candidate_since: datetime | None = None
    logged_status: str | None = None


class LeakStateStore:
    def __init__(self) -> None:
        self._states: dict[str, _LeakRuntimeState] = {}

    def state_for(self, sensor_key: str) -> _LeakRuntimeState:
        state = self._states.get(sensor_key)
        if state is None:
            state = _LeakRuntimeState()
            self._states[sensor_key] = state
        return state


DEFAULT_LEAK_STATE_STORE = LeakStateStore()


def read_all_leak_sensors(
    config: AppConfig,
    *,
    include_hidden: bool = False,
    state_store: LeakStateStore = DEFAULT_LEAK_STATE_STORE,
    now: datetime | None = None,
) -> list[LeakReading]:
    readings: list[LeakReading] = []
    for sensor_config in _leak_sensor_configs(config, include_hidden=include_hidden):
        readings.append(read_leak_sensor(sensor_config, state_store=state_store, now=now))
    return readings


def read_leak_sensor(
    sensor_config: LeakSensorConfig,
    *,
    state_store: LeakStateStore = DEFAULT_LEAK_STATE_STORE,
    now: datetime | None = None,
) -> LeakReading:
    state = state_store.state_for(sensor_config.sensor_key)
    current = now or datetime.now().astimezone()
    if state.latest is not None and state.latest.measured_at is not None:
        elapsed = (current - state.latest.measured_at).total_seconds()
        if elapsed < sensor_config.read_interval_seconds:
            return state.latest

    try:
        raw_value = read_leak_raw_gpio(sensor_config)
    except Exception as exc:
        print(
            f"WARN  leak sensor read failed: sensor={sensor_config.sensor_key} error={exc}",
            file=sys.stderr,
        )
        reading = _leak_reading(
            sensor_config,
            status=LEAK_STATUS_UNKNOWN,
            raw_value=None,
            measured_at=current,
            error=str(exc),
        )
        state.latest = reading
        return reading

    raw_status = leak_status_from_raw(raw_value, active_state=sensor_config.active_state)
    status = debounced_leak_status(
        state,
        raw_status=raw_status,
        measured_at=current,
        debounce_seconds=sensor_config.debounce_seconds,
    )
    reading = _leak_reading(
        sensor_config,
        status=status,
        raw_value=raw_value,
        measured_at=current,
    )
    state.latest = reading
    _log_status_change(sensor_config, state, status)
    return reading


def read_leak_raw_gpio(sensor_config: LeakSensorConfig) -> int:
    from gpiozero import InputDevice, OutputDevice

    drive = None
    sense = None
    try:
        drive = OutputDevice(sensor_config.drive_gpio, active_high=True, initial_value=False)
        sense = InputDevice(sensor_config.sense_gpio, pull_up=_gpiozero_pull_up(sensor_config.pull))
        drive.on()
        return 1 if sense.is_active else 0
    finally:
        try:
            if drive is not None:
                drive.off()
        finally:
            try:
                if sense is not None:
                    sense.close()
            finally:
                if drive is not None:
                    drive.close()


def leak_status_from_raw(raw_value: int, *, active_state: str) -> str:
    active_raw = 1 if active_state == "high" else 0
    return LEAK_STATUS_WET if raw_value == active_raw else LEAK_STATUS_DRY


def debounced_leak_status(
    state: _LeakRuntimeState,
    *,
    raw_status: str,
    measured_at: datetime,
    debounce_seconds: int,
) -> str:
    if state.stable_status is None:
        state.stable_status = raw_status
        state.candidate_status = None
        state.candidate_since = None
        return raw_status

    if raw_status == state.stable_status:
        state.candidate_status = None
        state.candidate_since = None
        return state.stable_status

    if state.candidate_status != raw_status:
        state.candidate_status = raw_status
        state.candidate_since = measured_at
        return state.stable_status

    if state.candidate_since is not None:
        elapsed = (measured_at - state.candidate_since).total_seconds()
        if elapsed >= debounce_seconds:
            state.stable_status = raw_status
            state.candidate_status = None
            state.candidate_since = None

    return state.stable_status


def leak_reading_to_dict(reading: LeakReading) -> dict[str, object]:
    return {
        "sensor_key": reading.sensor_key,
        "name": reading.name,
        "short_name": reading.short_name,
        "short_name_ascii": reading.short_name_ascii,
        "type": reading.type,
        "role": reading.role,
        "enabled": reading.enabled,
        "visible": reading.visible,
        "sort_order": reading.sort_order,
        "status": reading.status,
        "alert": reading.alert,
        "raw_value": reading.raw_value,
        "measured_at": reading.measured_at.astimezone().isoformat(timespec="seconds")
        if reading.measured_at is not None
        else None,
        "error": reading.error,
    }


def unknown_leak_readings(config: AppConfig, *, include_hidden: bool = False) -> list[LeakReading]:
    return [
        _leak_reading(
            sensor_config,
            status=LEAK_STATUS_UNKNOWN,
            raw_value=None,
            measured_at=None,
        )
        for sensor_config in _leak_sensor_configs(config, include_hidden=include_hidden)
    ]


def _leak_sensor_configs(config: AppConfig, *, include_hidden: bool) -> list[LeakSensorConfig]:
    sensors = [
        sensor_config
        for sensor_config in config.configured_leak_sensors().values()
        if sensor_config.enabled and (include_hidden or sensor_config.visible)
    ]
    return sorted(sensors, key=lambda sensor: (sensor.sort_order, sensor.name, sensor.sensor_key))


def _leak_reading(
    sensor_config: LeakSensorConfig,
    *,
    status: str,
    raw_value: int | None,
    measured_at: datetime | None,
    error: str | None = None,
) -> LeakReading:
    return LeakReading(
        sensor_key=sensor_config.sensor_key,
        name=sensor_config.name,
        short_name=sensor_config.short_name,
        short_name_ascii=sensor_config.short_name_ascii,
        type=sensor_config.type,
        role=sensor_config.role,
        enabled=sensor_config.enabled,
        visible=sensor_config.visible,
        sort_order=sensor_config.sort_order,
        status=status,
        alert=status == LEAK_STATUS_WET,
        raw_value=raw_value,
        measured_at=measured_at,
        error=error,
    )


def _gpiozero_pull_up(pull: str) -> bool | None:
    if pull == "up":
        return True
    if pull == "down":
        return False
    return None


def _log_status_change(
    sensor_config: LeakSensorConfig,
    state: _LeakRuntimeState,
    status: str,
) -> None:
    if state.logged_status == status:
        return
    state.logged_status = status
    level = "WARN" if status == LEAK_STATUS_WET else "INFO"
    print(
        f"{level}  leak sensor status changed: sensor={sensor_config.sensor_key} status={status}",
        file=sys.stderr,
    )
