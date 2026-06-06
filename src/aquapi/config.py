from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SensorConfig:
    sensor_id: str
    name: str
    type: str
    offset: float
    min: float | None
    max: float | None


@dataclass(frozen=True)
class AppConfig:
    sensors: dict[str, SensorConfig]
    listen_addr: str = "0.0.0.0"
    listen_port: int = 8081

    def find_sensor(self, sensor_id: str) -> SensorConfig | None:
        return self.sensors.get(sensor_id)


def load_config(path: Path) -> AppConfig:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("設定ファイルのルートは JSON object である必要があります")

    sensors_data = data.get("sensors")
    if not isinstance(sensors_data, dict):
        raise ValueError("設定ファイルに sensors object が必要です")

    sensors: dict[str, SensorConfig] = {}
    for sensor_id, raw_config in sensors_data.items():
        if not isinstance(sensor_id, str):
            raise ValueError("センサーIDは文字列である必要があります")
        if not isinstance(raw_config, dict):
            raise ValueError(f"{sensor_id}: センサー設定は object である必要があります")

        sensors[sensor_id] = SensorConfig(
            sensor_id=sensor_id,
            name=_required_str(raw_config, "name", sensor_id),
            type=_required_str(raw_config, "type", sensor_id),
            offset=_optional_float(raw_config, "offset", sensor_id, default=0.0),
            min=_optional_float(raw_config, "min", sensor_id),
            max=_optional_float(raw_config, "max", sensor_id),
        )

    return AppConfig(
        sensors=sensors,
        listen_addr=_optional_str(data, "listen_addr", default="0.0.0.0"),
        listen_port=_optional_int(data, "listen_port", default=8081),
    )


def _required_str(data: dict[str, Any], key: str, sensor_id: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or value == "":
        raise ValueError(f"{sensor_id}: {key} は空でない文字列である必要があります")
    return value


def _optional_str(data: dict[str, Any], key: str, *, default: str) -> str:
    value = data.get(key, default)
    if not isinstance(value, str) or value == "":
        raise ValueError(f"{key} は空でない文字列である必要があります")
    return value


def _optional_int(data: dict[str, Any], key: str, *, default: int) -> int:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} は整数である必要があります")
    if value < 1 or value > 65535:
        raise ValueError(f"{key} は 1 から 65535 の範囲である必要があります")
    return value


def _optional_float(
    data: dict[str, Any],
    key: str,
    sensor_id: str,
    *,
    default: float | None = None,
) -> float | None:
    value = data.get(key, default)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{sensor_id}: {key} は数値または null である必要があります")
    return float(value)
