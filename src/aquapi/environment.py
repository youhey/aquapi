from __future__ import annotations

import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from aquapi.config import AppConfig, EnvironmentSensorConfig


@dataclass(frozen=True)
class EnvironmentReading:
    sensor_key: str
    name: str
    short_name: str
    short_name_ascii: str
    type: str
    role: str
    enabled: bool
    visible: bool
    sort_order: int
    temperature_c: float | None
    relative_humidity_percent: float | None
    crc_ok: bool
    error: str | None = None


class I2CBus(Protocol):
    def write_i2c_block_data(self, i2c_addr: int, register: int, data: list[int]) -> None:
        ...

    def read_i2c_block_data(self, i2c_addr: int, register: int, length: int) -> list[int]:
        ...


class SHT31ReadError(RuntimeError):
    pass


def read_all_environment_sensors(config: AppConfig) -> list[EnvironmentReading]:
    readings: list[EnvironmentReading] = []
    for sensor_config in config.configured_environment_sensors().values():
        if not sensor_config.enabled:
            continue
        readings.append(read_environment_sensor(sensor_config))
    return readings


def read_environment_sensor(sensor_config: EnvironmentSensorConfig) -> EnvironmentReading:
    try:
        temperature_c, humidity_percent = read_sht31(sensor_config)
    except (OSError, RuntimeError, ImportError) as exc:
        print(
            f"warning: environment sensor read failed: sensor={sensor_config.sensor_key} error={exc}",
            file=sys.stderr,
        )
        return _environment_error_reading(sensor_config, str(exc))

    print(
        "info: environment sensor read ok: "
        f"sensor={sensor_config.sensor_key} temp={temperature_c:.2f} humidity={humidity_percent:.2f}",
        file=sys.stderr,
    )
    return _environment_reading(sensor_config, temperature_c, humidity_percent)


def read_sht31(sensor_config: EnvironmentSensorConfig) -> tuple[float, float]:
    bus = _open_i2c_bus(sensor_config.i2c_bus)
    try:
        return read_sht31_from_bus(bus, sensor_config.i2c_address)
    finally:
        close = getattr(bus, "close", None)
        if callable(close):
            close()


def read_sht31_from_bus(bus: I2CBus, i2c_address: int) -> tuple[float, float]:
    bus.write_i2c_block_data(i2c_address, 0x24, [0x00])
    time.sleep(0.015)
    raw = bus.read_i2c_block_data(i2c_address, 0x00, 6)
    if len(raw) != 6:
        raise SHT31ReadError("sht31 response length mismatch")
    if not sht31_crc_ok(raw[0:2], raw[2]):
        raise SHT31ReadError("temperature crc mismatch")
    if not sht31_crc_ok(raw[3:5], raw[5]):
        raise SHT31ReadError("humidity crc mismatch")

    raw_temperature = (raw[0] << 8) | raw[1]
    raw_humidity = (raw[3] << 8) | raw[4]
    return sht31_temperature_c(raw_temperature), sht31_humidity_percent(raw_humidity)


def sht31_temperature_c(raw_temperature: int) -> float:
    return -45.0 + 175.0 * raw_temperature / 65535.0


def sht31_humidity_percent(raw_humidity: int) -> float:
    return 100.0 * raw_humidity / 65535.0


def sht31_crc(data: list[int]) -> int:
    crc = 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x31) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def sht31_crc_ok(data: list[int], expected_crc: int) -> bool:
    return sht31_crc(data) == expected_crc


def environment_log_once(
    config: AppConfig,
    *,
    now: datetime | None = None,
    readings: list[EnvironmentReading] | None = None,
):
    from aquapi.sqlite_storage import SQLiteStorage

    timestamp = now or datetime.now().astimezone()
    current_readings = readings if readings is not None else read_all_environment_sensors(config)
    storage = SQLiteStorage(config.logging.database_path)
    result = storage.insert_environment_readings(current_readings, timestamp)
    return result


def collect_environment_if_due(
    config: AppConfig,
    *,
    now: datetime,
    last_read_at: datetime | None,
) -> datetime | None:
    if config.logging.storage != "sqlite":
        return last_read_at

    enabled = [
        sensor_config
        for sensor_config in config.configured_environment_sensors().values()
        if sensor_config.enabled
    ]
    if not enabled:
        return last_read_at

    interval = min(sensor_config.read_interval_seconds for sensor_config in enabled)
    if last_read_at is not None and (now - last_read_at).total_seconds() < interval:
        return last_read_at

    try:
        environment_log_once(config, now=now)
    except (OSError, sqlite3.Error, RuntimeError) as exc:
        print(f"error: environment collection failed: {exc}", file=sys.stderr)
    return now


def _open_i2c_bus(i2c_bus: int):
    try:
        from smbus2 import SMBus

        return SMBus(i2c_bus)
    except ImportError:
        from smbus import SMBus

        return SMBus(i2c_bus)


def _environment_reading(
    sensor_config: EnvironmentSensorConfig,
    temperature_c: float | None,
    humidity_percent: float | None,
    *,
    crc_ok: bool = True,
    error: str | None = None,
) -> EnvironmentReading:
    return EnvironmentReading(
        sensor_key=sensor_config.sensor_key,
        name=sensor_config.name,
        short_name=sensor_config.short_name,
        short_name_ascii=sensor_config.short_name_ascii,
        type=sensor_config.type,
        role=sensor_config.role,
        enabled=sensor_config.enabled,
        visible=sensor_config.visible,
        sort_order=sensor_config.sort_order,
        temperature_c=temperature_c,
        relative_humidity_percent=humidity_percent,
        crc_ok=crc_ok,
        error=error,
    )


def _environment_error_reading(sensor_config: EnvironmentSensorConfig, error: str) -> EnvironmentReading:
    return _environment_reading(
        sensor_config,
        None,
        None,
        crc_ok=False,
        error=error,
    )
