from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from aquapi.config import AppConfig, FanConfig
from aquapi.sensors import ConfiguredSensorReading


FAN_ON = "on"
FAN_OFF = "off"
FAN_UNKNOWN = "unknown"
FAN_DISABLED = "disabled"
AUTOMATIC_KEEP_ON_REASONS = {"temperature_above_start", "within_hysteresis_keep_on"}


@dataclass(frozen=True)
class FanState:
    fan_id: str
    name: str
    gpio: int
    active_high: bool
    enabled: bool
    state: str
    bound_tank_id: str | None
    reason: str
    last_changed_at: datetime | None = None
    temperature_c: float | None = None
    threshold_c: float | None = None
    error: str | None = None


def fan_state_to_dict(state: FanState) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": state.fan_id,
        "name": state.name,
        "gpio": state.gpio,
        "active_high": state.active_high,
        "enabled": state.enabled,
        "state": state.state,
        "bound_tank_id": state.bound_tank_id,
        "reason": state.reason,
        "last_changed_at": _datetime_to_iso(state.last_changed_at),
    }
    if state.temperature_c is not None:
        payload["temperature_c"] = state.temperature_c
    if state.threshold_c is not None:
        payload["threshold_c"] = state.threshold_c
    if state.error is not None:
        payload["error"] = state.error
    return payload


class FanStateStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self, config: AppConfig) -> dict[str, FanState]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        raw_fans = data.get("fans", [])
        if not isinstance(raw_fans, list):
            return {}

        states: dict[str, FanState] = {}
        for raw_state in raw_fans:
            if not isinstance(raw_state, dict):
                continue
            fan_id = raw_state.get("id")
            fan_config = config.configured_fans().get(fan_id) if isinstance(fan_id, str) else None
            if fan_config is None:
                continue
            states[fan_id] = FanState(
                fan_id=fan_id,
                name=fan_config.name,
                gpio=fan_config.gpio,
                active_high=fan_config.active_high,
                enabled=fan_config.enabled,
                state=_str_or_default(raw_state.get("state"), FAN_UNKNOWN),
                bound_tank_id=_optional_str(raw_state.get("bound_tank_id")),
                reason=_str_or_default(raw_state.get("reason"), "startup_off"),
                last_changed_at=_parse_datetime(raw_state.get("last_changed_at")),
                temperature_c=_optional_float(raw_state.get("temperature_c")),
                threshold_c=_optional_float(raw_state.get("threshold_c")),
                error=_optional_str(raw_state.get("error")),
            )
        return states

    def save(self, states: list[FanState]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        tmp_path.write_text(
            json.dumps(
                {
                    "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "fans": [fan_state_to_dict(state) for state in states],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        tmp_path.replace(self.path)


class GpioFanDriver:
    def __init__(self, fans: dict[str, FanConfig]):
        self._devices: dict[str, Any] = {}
        self.error: str | None = None
        try:
            from gpiozero import OutputDevice
        except Exception as exc:  # pragma: no cover - 実機依存
            self.error = str(exc)
            print(f"error: fan gpio init failed: error={exc}", file=sys.stderr)
            return

        for fan in fans.values():
            try:
                device = OutputDevice(
                    fan.gpio,
                    active_high=fan.active_high,
                    initial_value=False,
                )
                device.off()
                self._devices[fan.fan_id] = device
            except Exception as exc:  # pragma: no cover - 実機依存
                self.error = str(exc)
                print(f"error: fan gpio init failed: fan={fan.fan_id} gpio={fan.gpio} error={exc}", file=sys.stderr)

    @property
    def available(self) -> bool:
        return self.error is None

    def set_state(self, fan_id: str, state: str) -> str | None:
        device = self._devices.get(fan_id)
        if device is None:
            return self.error or "fan gpio device is not initialized"
        try:
            if state == FAN_ON:
                device.on()
            else:
                device.off()
        except Exception as exc:  # pragma: no cover - 実機依存
            return str(exc)
        return None

    def all_off(self) -> None:
        for fan_id in list(self._devices):
            error = self.set_state(fan_id, FAN_OFF)
            if error is not None:
                print(f"error: fan gpio off failed: fan={fan_id} error={error}", file=sys.stderr)

    def close(self, *, turn_off: bool = True) -> None:
        if turn_off:
            self.all_off()
        for device in self._devices.values():
            try:
                device.close()
            except Exception:  # pragma: no cover - 実機依存
                pass
        self._devices.clear()


class FanController:
    def __init__(
        self,
        config: AppConfig,
        *,
        state_store: FanStateStore | None = None,
        driver: GpioFanDriver | None = None,
    ):
        self.config = config
        self.state_store = state_store or FanStateStore(default_fan_state_path(config))
        self.driver = driver or GpioFanDriver(config.configured_fans())
        self.driver.all_off()

    def apply(self, readings: list[ConfiguredSensorReading], *, now: datetime | None = None) -> list[FanState]:
        current = now or datetime.now().astimezone()
        previous = self.state_store.load(self.config)
        states = build_fan_states(self.config, readings, previous_states=previous, now=current)
        applied_states: list[FanState] = []
        for state in states:
            target_state = FAN_ON if state.state == FAN_ON else FAN_OFF
            error = self.driver.set_state(state.fan_id, target_state)
            if error is not None:
                applied = _replace_state(state, new_state=FAN_UNKNOWN, reason="gpio_error", error=error, now=current)
            else:
                applied = state
            _log_state_change(previous.get(state.fan_id), applied)
            applied_states.append(applied)
        self.state_store.save(applied_states)
        return applied_states

    def close(self) -> None:
        current = datetime.now().astimezone()
        self.driver.close()
        previous = self.state_store.load(self.config)
        states = [
            FanState(
                fan_id=fan.fan_id,
                name=fan.name,
                gpio=fan.gpio,
                active_high=fan.active_high,
                enabled=fan.enabled,
                state=FAN_DISABLED if not fan.enabled else FAN_OFF,
                bound_tank_id=_bound_tank_id(self.config, fan.fan_id),
                reason="shutdown_off",
                last_changed_at=current,
            )
            for fan in sorted(self.config.configured_fans().values(), key=lambda item: item.fan_id)
        ]
        for state in states:
            _log_state_change(previous.get(state.fan_id), state)
        self.state_store.save(states)


def default_fan_state_path(config: AppConfig) -> Path:
    if config.logging.storage == "sqlite":
        return config.logging.database_path.parent / "fan-state.json"
    return config.logging.data_dir / "fan-state.json"


def build_fan_states(
    config: AppConfig,
    readings: list[ConfiguredSensorReading],
    *,
    previous_states: dict[str, FanState] | None = None,
    now: datetime | None = None,
) -> list[FanState]:
    current = now or datetime.now().astimezone()
    previous = previous_states or {}
    readings_by_fan = _readings_by_fan(config, readings)
    states: list[FanState] = []
    for fan in sorted(config.configured_fans().values(), key=lambda item: item.fan_id):
        reading = readings_by_fan.get(fan.fan_id)
        state = _fan_state_for_reading(fan, reading, previous.get(fan.fan_id), current)
        states.append(state)
    return states


def temperature_alert_payload(reading: ConfiguredSensorReading) -> dict[str, object]:
    alert = reading.temperature_alert
    return {
        "enabled": alert.enabled,
        "too_hot_c": alert.too_hot_c,
        "too_cold_c": alert.too_cold_c,
        "state": temperature_alert_state(reading),
    }


def temperature_alert_state(reading: ConfiguredSensorReading) -> str:
    alert = reading.temperature_alert
    if not alert.enabled:
        return "disabled"
    if reading.temperature_c is None or reading.error is not None or not reading.crc_ok:
        return "unknown"
    if alert.too_hot_c is not None and reading.temperature_c >= alert.too_hot_c:
        return "hot"
    if alert.too_cold_c is not None and reading.temperature_c <= alert.too_cold_c:
        return "cold"
    return "ok"


def fan_control_payload(
    reading: ConfiguredSensorReading,
    fan_states: dict[str, FanState],
) -> dict[str, object]:
    fan_control = reading.fan_control
    fan_state = fan_states.get(fan_control.fan_id)
    return {
        "enabled": fan_control.enabled,
        "fan_id": fan_control.fan_id,
        "state": fan_state.state if fan_state is not None else ("disabled" if not fan_control.enabled else "unknown"),
        "start_c": fan_control.start_c,
        "stop_c": fan_control.stop_c,
        "reason": fan_state.reason if fan_state is not None else _fan_control_default_reason(reading),
    }


def compact_fan_payload(state: FanState) -> dict[str, object]:
    return {
        "id": state.fan_id,
        "state": state.state,
        "enabled": state.enabled,
    }


def fan_states_by_id(states: list[FanState]) -> dict[str, FanState]:
    return {state.fan_id: state for state in states}


def run_fan_test(config: AppConfig, *, sleep_seconds: float = 3.0) -> int:
    driver = GpioFanDriver(config.configured_fans())
    if not driver.available:
        print(f"error: fan gpio unavailable: {driver.error}", file=sys.stderr)
        return 1
    try:
        for fan in sorted(config.configured_fans().values(), key=lambda item: item.fan_id):
            if not fan.enabled:
                print(f"{fan.fan_id} disabled")
                continue
            print(f"{fan.fan_id} ON")
            error = driver.set_state(fan.fan_id, FAN_ON)
            if error is not None:
                print(f"error: {fan.fan_id}: {error}", file=sys.stderr)
                return 1
            time.sleep(sleep_seconds)
            print(f"{fan.fan_id} OFF")
            error = driver.set_state(fan.fan_id, FAN_OFF)
            if error is not None:
                print(f"error: {fan.fan_id}: {error}", file=sys.stderr)
                return 1
        return 0
    finally:
        driver.close()


def set_manual_fan_state(config: AppConfig, fan_id: str, state: str) -> int:
    fan = config.configured_fans().get(fan_id)
    if fan is None:
        print(f"error: fan not found: {fan_id}", file=sys.stderr)
        return 1
    driver = GpioFanDriver(config.configured_fans())
    if not driver.available:
        print(f"error: fan gpio unavailable: {driver.error}", file=sys.stderr)
        return 1
    try:
        error = driver.set_state(fan_id, state)
        if error is not None:
            print(f"error: {fan_id}: {error}", file=sys.stderr)
            return 1
        current = datetime.now().astimezone()
        store = FanStateStore(default_fan_state_path(config))
        previous = store.load(config)
        states = []
        for fan_config in sorted(config.configured_fans().values(), key=lambda item: item.fan_id):
            prev = previous.get(fan_config.fan_id)
            if fan_config.fan_id == fan_id:
                states.append(
                    FanState(
                        fan_id=fan_config.fan_id,
                        name=fan_config.name,
                        gpio=fan_config.gpio,
                        active_high=fan_config.active_high,
                        enabled=fan_config.enabled,
                        state=state,
                        bound_tank_id=_bound_tank_id(config, fan_config.fan_id),
                        reason="manual_on" if state == FAN_ON else "manual_off",
                        last_changed_at=current,
                    )
                )
            elif prev is not None:
                states.append(prev)
        store.save(states)
        print(f"{fan_id} {state}")
        return 0
    finally:
        driver.close(turn_off=state != FAN_ON)


def _readings_by_fan(
    config: AppConfig,
    readings: list[ConfiguredSensorReading],
) -> dict[str, ConfiguredSensorReading]:
    fan_ids = set(config.configured_fans())
    return {
        reading.fan_control.fan_id: reading
        for reading in readings
        if reading.fan_control.fan_id in fan_ids
    }


def _fan_state_for_reading(
    fan: FanConfig,
    reading: ConfiguredSensorReading | None,
    previous: FanState | None,
    now: datetime,
) -> FanState:
    if reading is None:
        return _base_state(fan, FAN_DISABLED if not fan.enabled else FAN_OFF, None, "startup_off", now)
    fan_control = reading.fan_control
    if not fan.enabled:
        return _base_state(fan, FAN_DISABLED, reading.sensor_id, "fan_disabled", now)
    if not fan_control.enabled:
        return _base_state(fan, FAN_OFF, reading.sensor_id, "tank_fan_control_disabled", now)
    if reading.temperature_c is None or reading.error is not None or not reading.crc_ok:
        return _base_state(fan, FAN_OFF, reading.sensor_id, "temperature_unknown", now)

    temperature = reading.temperature_c
    assert fan_control.start_c is not None
    assert fan_control.stop_c is not None
    if temperature >= fan_control.start_c:
        return _base_state(
            fan,
            FAN_ON,
            reading.sensor_id,
            "temperature_above_start",
            now,
            temperature_c=temperature,
            threshold_c=fan_control.start_c,
        )
    if temperature <= fan_control.stop_c:
        return _base_state(
            fan,
            FAN_OFF,
            reading.sensor_id,
            "temperature_below_stop",
            now,
            temperature_c=temperature,
            threshold_c=fan_control.stop_c,
        )

    keep_on = (
        previous is not None
        and previous.state == FAN_ON
        and previous.bound_tank_id == reading.sensor_id
        and previous.reason in AUTOMATIC_KEEP_ON_REASONS
    )
    return _base_state(
        fan,
        FAN_ON if keep_on else FAN_OFF,
        reading.sensor_id,
        "within_hysteresis_keep_on" if keep_on else "within_hysteresis_keep_off",
        now,
        temperature_c=temperature,
    )


def _base_state(
    fan: FanConfig,
    state: str,
    bound_tank_id: str | None,
    reason: str,
    now: datetime,
    *,
    temperature_c: float | None = None,
    threshold_c: float | None = None,
) -> FanState:
    return FanState(
        fan_id=fan.fan_id,
        name=fan.name,
        gpio=fan.gpio,
        active_high=fan.active_high,
        enabled=fan.enabled,
        state=state,
        bound_tank_id=bound_tank_id,
        reason=reason,
        last_changed_at=now,
        temperature_c=temperature_c,
        threshold_c=threshold_c,
    )


def _replace_state(
    fan_state: FanState,
    *,
    new_state: str,
    reason: str,
    error: str,
    now: datetime,
) -> FanState:
    return FanState(
        fan_id=fan_state.fan_id,
        name=fan_state.name,
        gpio=fan_state.gpio,
        active_high=fan_state.active_high,
        enabled=fan_state.enabled,
        state=new_state,
        bound_tank_id=fan_state.bound_tank_id,
        reason=reason,
        last_changed_at=now,
        temperature_c=fan_state.temperature_c,
        threshold_c=fan_state.threshold_c,
        error=error,
    )


def _fan_control_default_reason(reading: ConfiguredSensorReading) -> str:
    if not reading.fan_control.enabled:
        return "tank_fan_control_disabled"
    return "startup_off"


def _bound_tank_id(config: AppConfig, fan_id: str) -> str | None:
    for sensor_id, sensor_config in config.sensors.items():
        if sensor_config.fan_control.fan_id == fan_id:
            return sensor_id
    return None


def _log_state_change(previous: FanState | None, current: FanState) -> None:
    if previous is not None and previous.state == current.state and previous.reason == current.reason:
        return
    event = {
        "measured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "event": "fan_state_changed",
        "fan_id": current.fan_id,
        "tank_id": current.bound_tank_id,
        "from": previous.state if previous is not None else None,
        "to": current.state,
        "reason": current.reason,
        "temperature_c": current.temperature_c,
        "threshold_c": current.threshold_c,
    }
    print(json.dumps(event, ensure_ascii=False, separators=(",", ":")), file=sys.stderr)


def _datetime_to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone().isoformat(timespec="seconds")


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _str_or_default(value: object, default: str) -> str:
    return value if isinstance(value, str) and value else default


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)
