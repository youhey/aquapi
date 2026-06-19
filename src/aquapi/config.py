from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TemperatureAlertConfig:
    enabled: bool = False
    too_hot_c: float | None = None
    too_cold_c: float | None = None


@dataclass(frozen=True)
class FanControlConfig:
    enabled: bool = False
    fan_id: str = ""
    start_c: float | None = None
    stop_c: float | None = None


@dataclass(frozen=True)
class FanConfig:
    fan_id: str
    name: str
    gpio: int
    active_high: bool = True
    enabled: bool = True


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
    display_code: str = ""
    temperature_alert: TemperatureAlertConfig = field(default_factory=TemperatureAlertConfig)
    fan_control: FanControlConfig = field(default_factory=FanControlConfig)

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
class EnvironmentSensorConfig:
    sensor_key: str
    name: str
    type: str
    role: str
    enabled: bool
    visible: bool
    sort_order: int
    i2c_bus: int
    i2c_address: int
    read_interval_seconds: int
    short_name: str = ""
    short_name_ascii: str = ""

    def __post_init__(self) -> None:
        if self.short_name == "":
            object.__setattr__(self, "short_name", default_short_name(self.name))
        if self.short_name_ascii == "":
            object.__setattr__(
                self,
                "short_name_ascii",
                default_short_name_ascii(short_name=self.short_name, name=self.name),
            )


@dataclass(frozen=True)
class LeakSensorConfig:
    sensor_key: str
    name: str
    type: str
    role: str
    enabled: bool
    visible: bool
    sort_order: int
    drive_gpio: int
    sense_gpio: int
    pull: str
    active_state: str
    read_interval_seconds: int
    debounce_seconds: int
    short_name: str = ""
    short_name_ascii: str = ""

    def __post_init__(self) -> None:
        if self.short_name == "":
            object.__setattr__(self, "short_name", default_short_name(self.name))
        if self.short_name_ascii == "":
            object.__setattr__(
                self,
                "short_name_ascii",
                default_short_name_ascii(short_name=self.short_name, name=self.name),
            )


@dataclass(frozen=True)
class AppConfig:
    sensors: dict[str, SensorConfig]
    fans: dict[str, FanConfig] | None = None
    environment_sensors: dict[str, EnvironmentSensorConfig] | None = None
    leak_sensors: dict[str, LeakSensorConfig] | None = None
    listen_addr: str = "0.0.0.0"
    listen_port: int = 8080
    logging: LoggingConfig = LoggingConfig()
    weather: WeatherConfig = WeatherConfig()

    def find_sensor(self, sensor_id: str) -> SensorConfig | None:
        return self.sensors.get(sensor_id)

    def configured_environment_sensors(self) -> dict[str, EnvironmentSensorConfig]:
        return self.environment_sensors or {}

    def configured_leak_sensors(self) -> dict[str, LeakSensorConfig]:
        return self.leak_sensors or {}

    def configured_fans(self) -> dict[str, FanConfig]:
        return self.fans or {}


def load_config(path: Path) -> AppConfig:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("設定ファイルのルートは JSON object である必要があります")

    sensors_data = data.get("sensors")
    if not isinstance(sensors_data, dict):
        raise ValueError("設定ファイルに sensors object が必要です")

    fans = _load_fan_configs(data.get("fans"))
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
            display_code=_optional_display_code(raw_config, "display_code"),
            temperature_alert=_load_temperature_alert_config(
                raw_config.get("temperature_alert"),
                sensor_id=sensor_id,
                sensor_type=raw_config.get("type"),
            ),
            fan_control=_load_fan_control_config(
                raw_config.get("fan_control"),
                sensor_id=sensor_id,
                sensor_type=raw_config.get("type"),
                fans=fans,
            ),
        )

    _validate_unique_enabled_fan_bindings(sensors)

    return AppConfig(
        sensors=sensors,
        fans=fans,
        environment_sensors=_load_environment_sensor_configs(data.get("environment_sensors")),
        leak_sensors=_load_leak_sensor_configs(data.get("leak_sensors")),
        listen_addr=_optional_str(data, "listen_addr", default="0.0.0.0"),
        listen_port=_optional_int(data, "listen_port", default=8080),
        logging=_load_logging_config(data.get("logging")),
        weather=_load_weather_config(data.get("weather")),
    )


def _load_fan_configs(data: Any) -> dict[str, FanConfig]:
    if data is None:
        return {}
    if not isinstance(data, list):
        raise ValueError("fans は array である必要があります")

    fans: dict[str, FanConfig] = {}
    for index, raw_config in enumerate(data):
        context = f"fans[{index}]"
        if not isinstance(raw_config, dict):
            raise ValueError(f"{context}: fan 設定は object である必要があります")
        fan_id = _required_str(raw_config, "id", context)
        if fan_id in fans:
            raise ValueError(f"{context}: fan.id が重複しています: {fan_id}")
        fans[fan_id] = FanConfig(
            fan_id=fan_id,
            name=_optional_str(raw_config, "name", default=fan_id),
            gpio=_optional_int(raw_config, "gpio", default=22),
            active_high=_optional_bool(raw_config, "active_high", default=True),
            enabled=_optional_bool(raw_config, "enabled", default=True),
        )
    return fans


def _load_temperature_alert_config(
    data: Any,
    *,
    sensor_id: str,
    sensor_type: object,
) -> TemperatureAlertConfig:
    if data is None:
        return TemperatureAlertConfig()
    if not isinstance(data, dict):
        raise ValueError(f"{sensor_id}: temperature_alert は object である必要があります")

    enabled = _optional_bool(data, "enabled", default=False)
    if enabled and sensor_type != "water":
        raise ValueError(f"{sensor_id}: water 以外のセンサーでは temperature_alert.enabled=true にできません")
    too_hot_c = _required_float(data, "too_hot_c", sensor_id) if enabled else _optional_float(data, "too_hot_c", sensor_id)
    too_cold_c = (
        _required_float(data, "too_cold_c", sensor_id) if enabled else _optional_float(data, "too_cold_c", sensor_id)
    )
    if too_hot_c is not None and too_cold_c is not None and too_hot_c <= too_cold_c:
        raise ValueError(f"{sensor_id}: temperature_alert.too_hot_c は too_cold_c より大きい必要があります")
    return TemperatureAlertConfig(enabled=enabled, too_hot_c=too_hot_c, too_cold_c=too_cold_c)


def _load_fan_control_config(
    data: Any,
    *,
    sensor_id: str,
    sensor_type: object,
    fans: dict[str, FanConfig],
) -> FanControlConfig:
    if data is None:
        return FanControlConfig()
    if not isinstance(data, dict):
        raise ValueError(f"{sensor_id}: fan_control は object である必要があります")

    enabled = _optional_bool(data, "enabled", default=False)
    if enabled and sensor_type != "water":
        raise ValueError(f"{sensor_id}: water 以外のセンサーでは fan_control.enabled=true にできません")
    if not enabled:
        return FanControlConfig(
            enabled=False,
            fan_id=_optional_str_or_empty(data, "fan_id"),
            start_c=_optional_float(data, "start_c", sensor_id),
            stop_c=_optional_float(data, "stop_c", sensor_id),
        )

    fan_id = _required_str(data, "fan_id", sensor_id)
    if fan_id not in fans:
        raise ValueError(f"{sensor_id}: fan_control.fan_id が fans に存在しません: {fan_id}")
    start_c = _required_float(data, "start_c", sensor_id)
    stop_c = _required_float(data, "stop_c", sensor_id)
    if start_c <= stop_c:
        raise ValueError(f"{sensor_id}: fan_control.start_c は stop_c より大きい必要があります")
    return FanControlConfig(enabled=True, fan_id=fan_id, start_c=start_c, stop_c=stop_c)


def _validate_unique_enabled_fan_bindings(sensors: dict[str, SensorConfig]) -> None:
    bound: dict[str, str] = {}
    for sensor_id, sensor_config in sensors.items():
        fan_control = sensor_config.fan_control
        if not fan_control.enabled:
            continue
        if fan_control.fan_id in bound:
            raise ValueError(
                f"{sensor_id}: fan_control.fan_id が複数センサーに紐づいています: {fan_control.fan_id}"
            )
        bound[fan_control.fan_id] = sensor_id


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


def _load_environment_sensor_configs(data: Any) -> dict[str, EnvironmentSensorConfig]:
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("environment_sensors は object である必要があります")

    sensors: dict[str, EnvironmentSensorConfig] = {}
    for sensor_key, raw_config in data.items():
        if not isinstance(sensor_key, str) or sensor_key == "":
            raise ValueError("environment sensor key は空でない文字列である必要があります")
        if not isinstance(raw_config, dict):
            raise ValueError(f"{sensor_key}: environment sensor 設定は object である必要があります")
        sensors[sensor_key] = EnvironmentSensorConfig(
            sensor_key=sensor_key,
            name=_required_str(raw_config, "name", sensor_key),
            type=_required_str(raw_config, "type", sensor_key),
            role=_optional_role(raw_config, "role", default="indoor"),
            enabled=_optional_bool(raw_config, "enabled", default=True),
            visible=_optional_bool(raw_config, "visible", default=True),
            sort_order=_optional_sort_order(raw_config, "sort_order", default=1000),
            i2c_bus=_optional_int(raw_config, "i2c_bus", default=1),
            i2c_address=_optional_i2c_address(raw_config, "i2c_address", default=0x44),
            read_interval_seconds=_optional_int(raw_config, "read_interval_seconds", default=60),
            short_name=_optional_short_name(raw_config, "short_name"),
            short_name_ascii=_optional_short_name_ascii(raw_config, "short_name_ascii"),
        )
    return sensors


def _load_leak_sensor_configs(data: Any) -> dict[str, LeakSensorConfig]:
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("leak_sensors は object である必要があります")

    sensors: dict[str, LeakSensorConfig] = {}
    for sensor_key, raw_config in data.items():
        if not isinstance(sensor_key, str) or sensor_key == "":
            raise ValueError("leak sensor key は空でない文字列である必要があります")
        if not isinstance(raw_config, dict):
            raise ValueError(f"{sensor_key}: leak sensor 設定は object である必要があります")
        sensors[sensor_key] = LeakSensorConfig(
            sensor_key=sensor_key,
            name=_required_str(raw_config, "name", sensor_key),
            type=_required_str(raw_config, "type", sensor_key),
            role=_optional_role(raw_config, "role", default="leak"),
            enabled=_optional_bool(raw_config, "enabled", default=True),
            visible=_optional_bool(raw_config, "visible", default=True),
            sort_order=_optional_sort_order(raw_config, "sort_order", default=1000),
            drive_gpio=_optional_int(raw_config, "drive_gpio", default=17),
            sense_gpio=_optional_int(raw_config, "sense_gpio", default=27),
            pull=_optional_choice(raw_config, "pull", default="down", choices={"down", "up", "none"}),
            active_state=_optional_choice(
                raw_config,
                "active_state",
                default="high",
                choices={"high", "low"},
            ),
            read_interval_seconds=_optional_int(raw_config, "read_interval_seconds", default=5),
            debounce_seconds=_optional_int(raw_config, "debounce_seconds", default=2),
            short_name=_optional_short_name(raw_config, "short_name"),
            short_name_ascii=_optional_short_name_ascii(raw_config, "short_name_ascii"),
        )
    return sensors


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


def _optional_str_or_empty(data: dict[str, Any], key: str) -> str:
    value = data.get(key, "")
    if value == "":
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{key} は文字列である必要があります")
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
    if value not in {"aquarium", "outdoor", "indoor", "leak", "disabled", "unknown"}:
        raise ValueError(
            f"{key} は aquarium, outdoor, indoor, leak, disabled, unknown のいずれかである必要があります"
        )
    return value


def _optional_choice(
    data: dict[str, Any],
    key: str,
    *,
    default: str,
    choices: set[str],
) -> str:
    value = _optional_str(data, key, default=default)
    if value not in choices:
        allowed = ", ".join(sorted(choices))
        raise ValueError(f"{key} は {allowed} のいずれかである必要があります")
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


def _optional_display_code(data: dict[str, Any], key: str) -> str:
    value = data.get(key, "")
    if value == "":
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{key} は文字列である必要があります")
    if not value.isascii():
        raise ValueError(f"{key} は ASCII 文字列である必要があります")
    if len(value) != 3:
        raise ValueError(f"{key} は 3 文字である必要があります")
    return value


def _optional_i2c_address(data: dict[str, Any], key: str, *, default: int) -> int:
    value = data.get(key, default)
    if isinstance(value, bool):
        raise ValueError(f"{key} は整数または 0x 形式の文字列である必要があります")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 0)
        except ValueError as exc:
            raise ValueError(f"{key} は整数または 0x 形式の文字列である必要があります") from exc
    raise ValueError(f"{key} は整数または 0x 形式の文字列である必要があります")


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


def _required_float(data: dict[str, Any], key: str, context: str) -> float:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{context}: {key} は数値である必要があります")
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
