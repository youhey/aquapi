from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from aquapi.config import AppConfig, SensorConfig


DEFAULT_W1_BASE_PATH = Path("/sys/bus/w1/devices")
TEMPERATURE_RE = re.compile(r"\bt=(-?\d+)\b")


class SensorReadError(RuntimeError):
    """センサー読み取り値として採用できない場合のエラーです。"""


@dataclass(frozen=True)
class SensorReading:
    sensor_id: str
    temperature_c: float
    crc_ok: bool
    raw: str


@dataclass(frozen=True)
class ConfiguredSensorReading:
    sensor_id: str
    name: str
    type: str
    raw_temperature_c: float | None
    temperature_c: float | None
    offset: float
    min: float | None
    max: float | None
    status: str
    crc_ok: bool
    raw: str
    error: str | None = None
    role: str = "unknown"
    enabled: bool = True
    visible: bool = True
    sort_order: int = 1000
    short_name: str = ""
    short_name_ascii: str = ""
    display_code: str = ""


def configured_sensor_reading_to_dict(
    reading: ConfiguredSensorReading,
) -> dict[str, object]:
    payload: dict[str, object] = {
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
        "raw_temperature_c": reading.raw_temperature_c,
        "temperature_c": reading.temperature_c,
        "offset": reading.offset,
        "min": reading.min,
        "max": reading.max,
        "status": reading.status,
        "crc_ok": reading.crc_ok,
    }
    if reading.error is not None:
        payload["error"] = reading.error
    return payload


def discover_sensor_paths(base_path: Path = DEFAULT_W1_BASE_PATH) -> list[Path]:
    return sorted(path for path in base_path.glob("28-*") if path.is_dir())


def read_sensor(sensor_path: Path) -> SensorReading:
    slave_path = sensor_path / "w1_slave"
    raw = slave_path.read_text(encoding="utf-8").strip()

    lines = raw.splitlines()
    if len(lines) < 2:
        raise SensorReadError(f"{sensor_path.name}: w1_slave の形式が不正です")

    crc_ok = lines[0].rstrip().endswith("YES")
    if not crc_ok:
        raise SensorReadError(f"{sensor_path.name}: CRC チェックが失敗しました")

    match = TEMPERATURE_RE.search(lines[1])
    if match is None:
        raise SensorReadError(f"{sensor_path.name}: 温度データ t= が見つかりません")

    temperature_c = int(match.group(1)) / 1000.0

    return SensorReading(
        sensor_id=sensor_path.name,
        temperature_c=temperature_c,
        crc_ok=crc_ok,
        raw=raw,
    )


def read_all_sensors(base_path: Path = DEFAULT_W1_BASE_PATH) -> list[SensorReading]:
    return [read_sensor(sensor_path) for sensor_path in discover_sensor_paths(base_path)]


def apply_sensor_config(
    reading: SensorReading,
    sensor_config: SensorConfig | None,
) -> ConfiguredSensorReading:
    if sensor_config is None:
        return ConfiguredSensorReading(
            sensor_id=reading.sensor_id,
            name="unknown",
            type="unknown",
            role="unknown",
            enabled=True,
            visible=True,
            sort_order=1000,
            short_name="unknown",
            short_name_ascii="unknown",
            display_code="",
            raw_temperature_c=reading.temperature_c,
            temperature_c=reading.temperature_c,
            offset=0.0,
            min=None,
            max=None,
            status="unknown",
            crc_ok=reading.crc_ok,
            raw=reading.raw,
        )

    temperature_c = reading.temperature_c + sensor_config.offset

    return ConfiguredSensorReading(
        sensor_id=reading.sensor_id,
        name=sensor_config.name,
        type=sensor_config.type,
        role=sensor_config.role,
        enabled=sensor_config.enabled,
        visible=sensor_config.visible,
        sort_order=sensor_config.sort_order,
        short_name=sensor_config.short_name,
        short_name_ascii=sensor_config.short_name_ascii,
        display_code=sensor_config.display_code,
        raw_temperature_c=reading.temperature_c,
        temperature_c=temperature_c,
        offset=sensor_config.offset,
        min=sensor_config.min,
        max=sensor_config.max,
        status=_threshold_status(temperature_c, sensor_config),
        crc_ok=reading.crc_ok,
        raw=reading.raw,
    )


def read_all_configured_sensors(
    base_path: Path = DEFAULT_W1_BASE_PATH,
    config: AppConfig | None = None,
) -> list[ConfiguredSensorReading]:
    results: list[ConfiguredSensorReading] = []

    for sensor_path in discover_sensor_paths(base_path):
        sensor_config = config.find_sensor(sensor_path.name) if config is not None else None
        if sensor_config is not None and not sensor_config.enabled:
            continue
        try:
            reading = read_sensor(sensor_path)
        except (OSError, SensorReadError, ValueError) as exc:
            results.append(_error_reading(sensor_path.name, sensor_config, exc))
            continue

        results.append(apply_sensor_config(reading, sensor_config))

    return results


def _threshold_status(temperature_c: float, sensor_config: SensorConfig) -> str:
    if sensor_config.min is not None and temperature_c < sensor_config.min:
        return "low"
    if sensor_config.max is not None and temperature_c > sensor_config.max:
        return "high"
    return "ok"


def _error_reading(
    sensor_id: str,
    sensor_config: SensorConfig | None,
    exc: Exception,
) -> ConfiguredSensorReading:
    if sensor_config is None:
        name = "unknown"
        sensor_type = "unknown"
        role = "unknown"
        enabled = True
        visible = True
        sort_order = 1000
        short_name = "unknown"
        short_name_ascii = "unknown"
        display_code = ""
        offset = 0.0
        min_temperature = None
        max_temperature = None
    else:
        name = sensor_config.name
        sensor_type = sensor_config.type
        role = sensor_config.role
        enabled = sensor_config.enabled
        visible = sensor_config.visible
        sort_order = sensor_config.sort_order
        short_name = sensor_config.short_name
        short_name_ascii = sensor_config.short_name_ascii
        display_code = sensor_config.display_code
        offset = sensor_config.offset
        min_temperature = sensor_config.min
        max_temperature = sensor_config.max

    return ConfiguredSensorReading(
        sensor_id=sensor_id,
        name=name,
        type=sensor_type,
        role=role,
        enabled=enabled,
        visible=visible,
        sort_order=sort_order,
        short_name=short_name,
        short_name_ascii=short_name_ascii,
        display_code=display_code,
        raw_temperature_c=None,
        temperature_c=None,
        offset=offset,
        min=min_temperature,
        max=max_temperature,
        status="error",
        crc_ok=False,
        raw="",
        error=str(exc),
    )
