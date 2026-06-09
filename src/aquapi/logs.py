from __future__ import annotations

import json
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterator

from aquapi.config import AppConfig, LoggingConfig
from aquapi.environment import collect_environment_if_due
from aquapi.sensors import ConfiguredSensorReading, read_all_configured_sensors
from aquapi.sqlite_storage import SQLiteStorage


RANGE_DELTAS = {
    "1h": timedelta(hours=1),
    "6h": timedelta(hours=6),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "365d": timedelta(days=365),
    "1y": timedelta(days=365),
}


@dataclass(frozen=True)
class LogWriteResult:
    path: Path
    entry: dict[str, object]


def log_once(
    config: AppConfig,
    *,
    now: datetime | None = None,
    readings: list[ConfiguredSensorReading] | None = None,
) -> LogWriteResult:
    timestamp = now or _now()
    current_readings = readings if readings is not None else read_all_configured_sensors(config=config)
    return write_readings(config, current_readings, now=timestamp)


def collect_forever(config: AppConfig) -> None:
    last_environment_read_at: datetime | None = None
    while True:
        current = _now()
        try:
            log_once(config, now=current)
        except (OSError, sqlite3.Error) as exc:
            print(f"error: {exc}", file=sys.stderr)
        last_environment_read_at = collect_environment_if_due(
            config,
            now=current,
            last_read_at=last_environment_read_at,
        )
        time.sleep(config.logging.interval_seconds)


def write_readings(
    config: AppConfig,
    readings: list[ConfiguredSensorReading],
    *,
    now: datetime | None = None,
) -> LogWriteResult:
    if config.logging.storage == "jsonl":
        return append_readings(config.logging, readings, now=now)

    timestamp = now or _now()
    storage = SQLiteStorage(config.logging.database_path)
    storage.initialize()
    storage.sync_sensors(config)
    result = storage.insert_readings(readings, timestamp)
    storage.apply_retention(config.logging.retention_days, now=timestamp)
    return LogWriteResult(path=result.path, entry={"saved_count": result.saved_count})


def initialize_storage(config: AppConfig) -> None:
    if config.logging.storage == "jsonl":
        config.logging.data_dir.mkdir(parents=True, exist_ok=True)
        cleanup_old_logs(config.logging)
        return

    storage = SQLiteStorage(config.logging.database_path)
    storage.initialize()
    storage.sync_sensors(config)
    storage.apply_retention(config.logging.retention_days)


def append_readings(
    logging_config: LoggingConfig,
    readings: list[ConfiguredSensorReading],
    *,
    now: datetime | None = None,
) -> LogWriteResult:
    timestamp = now or _now()
    logging_config.data_dir.mkdir(parents=True, exist_ok=True)
    cleanup_old_logs(logging_config, today=timestamp.date())

    path = log_file_path(logging_config, timestamp)
    entry = build_log_entry(readings, timestamp)
    with path.open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")))
        log_file.write("\n")

    return LogWriteResult(path=path, entry=entry)


def build_log_entry(
    readings: list[ConfiguredSensorReading],
    timestamp: datetime,
) -> dict[str, object]:
    return {
        "ts": timestamp.astimezone().isoformat(timespec="seconds"),
        "sensors": [_reading_to_log_dict(reading) for reading in readings],
    }


def log_file_path(logging_config: LoggingConfig, timestamp: datetime) -> Path:
    return logging_config.data_dir / timestamp.strftime(logging_config.file_pattern)


def cleanup_old_logs(logging_config: LoggingConfig, *, today: date | None = None) -> None:
    if logging_config.retention_days <= 0:
        return
    if not logging_config.data_dir.exists():
        return

    current_date = today or _now().date()
    cutoff_date = current_date - timedelta(days=logging_config.retention_days)
    pattern = _file_pattern_regex(logging_config.file_pattern)

    for path in logging_config.data_dir.glob("*.jsonl"):
        match = pattern.fullmatch(path.name)
        if match is None:
            continue

        file_date = date(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
        )
        if file_date < cutoff_date:
            path.unlink()


def build_series_payload(
    logging_config: LoggingConfig,
    *,
    range_text: str,
    sensor_id: str | None = None,
    name: str | None = None,
    now: datetime | None = None,
) -> dict[str, object] | None:
    if logging_config.storage == "sqlite":
        end = now or _now()
        start = _range_start(range_text, now=end)
        return SQLiteStorage(logging_config.database_path).get_series(
            range_text=range_text,
            start=start,
            end=end,
            sensor_id=sensor_id,
            name=name,
        )

    since = _range_start(range_text, now=now)
    points: list[dict[str, object]] = []
    resolved_sensor_id: str | None = None
    resolved_name: str | None = None

    for entry in iter_log_entries(logging_config, since=since):
        ts = entry.get("ts")
        sensors = entry.get("sensors", [])
        if not isinstance(ts, str) or not isinstance(sensors, list):
            continue

        for sensor in sensors:
            if not isinstance(sensor, dict):
                continue
            if sensor_id is not None and sensor.get("sensor_id") != sensor_id:
                continue
            if name is not None and sensor.get("name") != name:
                continue

            resolved_sensor_id = str(sensor.get("sensor_id", ""))
            resolved_name = str(sensor.get("name", ""))
            points.append(
                {
                    "ts": ts,
                    "temperature_c": sensor.get("temperature_c"),
                    "status": sensor.get("status"),
                }
            )

    if not points:
        return None

    return {
        "sensor_id": resolved_sensor_id,
        "name": resolved_name,
        "range": range_text,
        "points": points,
    }


def build_history_summary_payload(
    logging_config: LoggingConfig,
    *,
    range_text: str,
    now: datetime | None = None,
) -> dict[str, object]:
    if logging_config.storage == "sqlite":
        end = now or _now()
        start = _range_start(range_text, now=end)
        return SQLiteStorage(logging_config.database_path).get_summary(
            range_text=range_text,
            start=start,
            end=end,
        )

    since = _range_start(range_text, now=now)
    by_sensor: dict[str, dict[str, object]] = {}

    for entry in iter_log_entries(logging_config, since=since):
        sensors = entry.get("sensors", [])
        if not isinstance(sensors, list):
            continue

        for sensor in sensors:
            if not isinstance(sensor, dict):
                continue
            sensor_id = sensor.get("sensor_id")
            if not isinstance(sensor_id, str):
                continue

            temperature = sensor.get("temperature_c")
            if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
                continue

            status = sensor.get("status")
            item = by_sensor.setdefault(
                sensor_id,
                {
                    "sensor_id": sensor_id,
                    "name": sensor.get("name", "unknown"),
                    "temperatures": [],
                    "latest_temperature_c": None,
                    "latest_status": None,
                },
            )
            temperatures = item["temperatures"]
            assert isinstance(temperatures, list)
            temperatures.append(float(temperature))
            item["latest_temperature_c"] = float(temperature)
            item["latest_status"] = status

    summaries = []
    for item in by_sensor.values():
        temperatures = item.pop("temperatures")
        assert isinstance(temperatures, list)
        summaries.append(
            {
                **item,
                "sample_count": len(temperatures),
                "min_temperature_c": min(temperatures),
                "avg_temperature_c": sum(temperatures) / len(temperatures),
                "max_temperature_c": max(temperatures),
            }
        )

    return {
        "range": range_text,
        "sensors": sorted(summaries, key=lambda item: str(item["sensor_id"])),
    }


def iter_log_entries(logging_config: LoggingConfig, *, since: datetime) -> Iterator[dict[str, object]]:
    if not logging_config.data_dir.exists():
        return

    for path in sorted(logging_config.data_dir.glob("*.jsonl")):
        with path.open(encoding="utf-8") as log_file:
            for line in log_file:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(entry, dict):
                    continue

                timestamp = _parse_timestamp(entry.get("ts"))
                if timestamp is None or timestamp < since:
                    continue

                yield entry


def _reading_to_log_dict(reading: ConfiguredSensorReading) -> dict[str, object]:
    payload: dict[str, object] = {
        "sensor_id": reading.sensor_id,
        "name": reading.name,
        "type": reading.type,
        "raw_temperature_c": reading.raw_temperature_c,
        "temperature_c": reading.temperature_c,
        "offset": reading.offset,
        "status": reading.status,
        "crc_ok": reading.crc_ok,
    }
    if reading.error is not None:
        payload["error"] = reading.error
    return payload


def _range_start(range_text: str, *, now: datetime | None = None) -> datetime:
    delta = RANGE_DELTAS.get(range_text)
    if delta is None:
        raise ValueError(f"unsupported range: {range_text}")
    return (now or _now()) - delta


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.astimezone()
    return parsed


def _file_pattern_regex(file_pattern: str) -> re.Pattern[str]:
    regex = re.escape(file_pattern)
    regex = regex.replace("%Y", r"(?P<year>\d{4})")
    regex = regex.replace("%m", r"(?P<month>\d{2})")
    regex = regex.replace("%d", r"(?P<day>\d{2})")
    return re.compile(regex)


def _now() -> datetime:
    return datetime.now().astimezone()
