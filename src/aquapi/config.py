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
    role: str = ""
    enabled: bool = True
    visible: bool = True
    sort_order: int = 1000
    short_name: str = ""
    short_name_ascii: str = ""

    def __post_init__(self) -> None:
        if self.role == "":
            object.__setattr__(self, "role", default_sensor_role(self.type))
        if self.short_name == "":
            object.__setattr__(self, "short_name", default_short_name(self.name))
        if self.short_name_ascii == "":
            object.__setattr__(
                self,
                "short_name_ascii",
                default_short_name_ascii(short_name=self.short_name, name=self.name),
            )


@dataclass(frozen=True)
class LoggingConfig:
    enabled: bool = False
    interval_seconds: int = 60
    storage: str = "sqlite"
    database_path: Path = Path("data/aquapi.sqlite3")
    data_dir: Path = Path("data")
    file_pattern: str = "readings-%Y-%m-%d.jsonl"
    retention_days: int = 365


@dataclass(frozen=True)
class WeatherConfig:
    enabled: bool = False
    source: str = "open-meteo"
    latitude: float = 35.681236
    longitude: float = 139.767125
    timezone: str = "Asia/Tokyo"
    interval_seconds: int = 3600
    forecast_days: int = 2
    retention_days: int = 365


@dataclass(frozen=True)
class AppConfig:
    sensors: dict[str, SensorConfig]
    listen_addr: str = "0.0.0.0"
    listen_port: int = 8080
    logging: LoggingConfig = LoggingConfig()
    weather: WeatherConfig = WeatherConfig()

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
            role=_optional_role(raw_config, "role", default=default_sensor_role(raw_config.get("type"))),
            enabled=_optional_bool(raw_config, "enabled", default=True),
            visible=_optional_bool(raw_config, "visible", default=True),
            sort_order=_optional_sort_order(raw_config, "sort_order", default=1000),
            short_name=_optional_short_name(raw_config, "short_name"),
            short_name_ascii=_optional_short_name_ascii(raw_config, "short_name_ascii"),
        )

    return AppConfig(
        sensors=sensors,
        listen_addr=_optional_str(data, "listen_addr", default="0.0.0.0"),
        listen_port=_optional_int(data, "listen_port", default=8080),
        logging=_load_logging_config(data.get("logging")),
        weather=_load_weather_config(data.get("weather")),
    )


def _load_logging_config(data: Any) -> LoggingConfig:
    if data is None:
        return LoggingConfig()
    if not isinstance(data, dict):
        raise ValueError("logging は object である必要があります")

    return LoggingConfig(
        enabled=_optional_bool(data, "enabled", default=False),
        interval_seconds=_optional_int(data, "interval_seconds", default=60),
        storage=_optional_storage(data, "storage", default="sqlite"),
        database_path=Path(_optional_str(data, "database_path", default="data/aquapi.sqlite3")),
        data_dir=Path(_optional_str(data, "data_dir", default="data")),
        file_pattern=_optional_str(data, "file_pattern", default="readings-%Y-%m-%d.jsonl"),
        retention_days=_optional_retention_days(data, "retention_days", default=365),
    )


def _load_weather_config(data: Any) -> WeatherConfig:
    if data is None:
        return WeatherConfig()
    if not isinstance(data, dict):
        raise ValueError("weather は object である必要があります")

    return WeatherConfig(
        enabled=_optional_bool(data, "enabled", default=False),
        source=_optional_weather_source(data, "source", default="open-meteo"),
        latitude=_optional_number(data, "latitude", default=35.681236),
        longitude=_optional_number(data, "longitude", default=139.767125),
        timezone=_optional_str(data, "timezone", default="Asia/Tokyo"),
        interval_seconds=_optional_int(data, "interval_seconds", default=3600),
        forecast_days=_optional_int(data, "forecast_days", default=2),
        retention_days=_optional_retention_days(data, "retention_days", default=365),
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


def _optional_retention_days(data: dict[str, Any], key: str, *, default: int) -> int:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} は整数である必要があります")
    return value


def _optional_storage(data: dict[str, Any], key: str, *, default: str) -> str:
    value = _optional_str(data, key, default=default)
    if value not in {"sqlite", "jsonl"}:
        raise ValueError(f"{key} は sqlite または jsonl である必要があります")
    return value


def _optional_weather_source(data: dict[str, Any], key: str, *, default: str) -> str:
    value = _optional_str(data, key, default=default)
    if value != "open-meteo":
        raise ValueError(f"{key} は open-meteo である必要があります")
    return value


def _optional_role(data: dict[str, Any], key: str, *, default: str) -> str:
    value = _optional_str(data, key, default=default)
    if value not in {"aquarium", "outdoor", "indoor", "disabled", "unknown"}:
        raise ValueError(f"{key} は aquarium, outdoor, indoor, disabled, unknown のいずれかである必要があります")
    return value


def _optional_sort_order(data: dict[str, Any], key: str, *, default: int) -> int:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} は整数である必要があります")
    return value


def _optional_short_name(data: dict[str, Any], key: str) -> str:
    value = data.get(key, "")
    if value == "":
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{key} は文字列である必要があります")
    return value


def _optional_short_name_ascii(data: dict[str, Any], key: str) -> str:
    value = data.get(key, "")
    if value == "":
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{key} は文字列である必要があります")
    if not value.isascii():
        raise ValueError(f"{key} は ASCII 文字列である必要があります")
    return value


def default_sensor_role(sensor_type: object) -> str:
    if sensor_type == "water":
        return "aquarium"
    if sensor_type == "air":
        return "outdoor"
    return "unknown"


def default_short_name(name: str) -> str:
    short_name = name.removesuffix("水槽")
    return short_name or name


def default_short_name_ascii(*, short_name: str, name: str) -> str:
    if short_name.isascii():
        return short_name
    if name.isascii():
        return name
    return ""


def _optional_number(data: dict[str, Any], key: str, *, default: float) -> float:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} は数値である必要があります")
    return float(value)


def _optional_bool(data: dict[str, Any], key: str, *, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} は bool である必要があります")
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
