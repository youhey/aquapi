from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from aquapi.api import serve_api
from aquapi.config import AppConfig, load_config
from aquapi.environment import EnvironmentReading, read_all_environment_sensors
from aquapi.fans import (
    FAN_OFF,
    FAN_ON,
    FanStateStore,
    default_fan_state_path,
    fan_state_to_dict,
    run_fan_test,
    set_manual_fan_state,
)
from aquapi.leak import leak_reading_to_dict, read_all_leak_sensors
from aquapi.logs import collect_forever, initialize_storage, log_once
from aquapi.sqlite_storage import SQLiteStorage
from aquapi.sensors import (
    configured_sensor_reading_to_dict,
    read_all_configured_sensors,
)
from aquapi.weather import collect_weather_forever, fetch_weather_once


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aquapi")
    subparsers = parser.add_subparsers(dest="command", required=True)

    read_parser = subparsers.add_parser("read", help="1-Wire 温度センサーを読み取ります")
    read_parser.add_argument("--json", action="store_true", help="JSON 形式で出力します")
    read_parser.add_argument("--config", type=Path, help="センサー設定 JSON のパス")

    read_environment_parser = subparsers.add_parser("read-environment", help="室内環境センサーを読み取ります")
    read_environment_parser.add_argument("--json", action="store_true", help="JSON 形式で出力します")
    read_environment_parser.add_argument("--config", type=Path, required=True, help="センサー設定 JSON のパス")

    read_leak_parser = subparsers.add_parser("read-leak", help="漏水センサーを読み取ります")
    read_leak_parser.add_argument("--json", action="store_true", help="JSON 形式で出力します")
    read_leak_parser.add_argument("--config", type=Path, required=True, help="センサー設定 JSON のパス")

    serve_parser = subparsers.add_parser("serve", help="JSON API サーバーを起動します")
    serve_parser.add_argument("--config", type=Path, required=True, help="センサー設定 JSON のパス")
    serve_parser.add_argument("--host", help="待ち受けホスト")
    serve_parser.add_argument("--port", type=int, help="待ち受けポート")

    log_once_parser = subparsers.add_parser("log-once", help="現在のセンサー値を1回だけ保存します")
    log_once_parser.add_argument("--config", type=Path, required=True, help="センサー設定 JSON のパス")

    collect_parser = subparsers.add_parser("collect", help="指定間隔でセンサー値を保存し続けます")
    collect_parser.add_argument("--config", type=Path, required=True, help="センサー設定 JSON のパス")

    db_init_parser = subparsers.add_parser("db-init", help="SQLite DB を初期化します")
    db_init_parser.add_argument("--config", type=Path, required=True, help="センサー設定 JSON のパス")

    db_stats_parser = subparsers.add_parser("db-stats", help="SQLite DB の保存状況を表示します")
    db_stats_parser.add_argument("--config", type=Path, required=True, help="センサー設定 JSON のパス")

    fetch_weather_parser = subparsers.add_parser(
        "fetch-weather-once",
        help="Open-Meteo の外部気象を1回だけ取得して保存します",
    )
    fetch_weather_parser.add_argument("--config", type=Path, required=True, help="センサー設定 JSON のパス")

    weather_collect_parser = subparsers.add_parser(
        "weather-collect",
        help="指定間隔で Open-Meteo の外部気象を取得して保存し続けます",
    )
    weather_collect_parser.add_argument("--config", type=Path, required=True, help="センサー設定 JSON のパス")

    fan_list_parser = subparsers.add_parser("fan:list", help="ファン設定と最新状態を表示します")
    fan_list_parser.add_argument("--json", action="store_true", help="JSON 形式で出力します")
    fan_list_parser.add_argument("--config", type=Path, required=True, help="センサー設定 JSON のパス")

    fan_on_parser = subparsers.add_parser("fan:on", help="指定ファンを手動でONにします")
    fan_on_parser.add_argument("fan_id", help="fan id")
    fan_on_parser.add_argument("--config", type=Path, required=True, help="センサー設定 JSON のパス")

    fan_off_parser = subparsers.add_parser("fan:off", help="指定ファンを手動でOFFにします")
    fan_off_parser.add_argument("fan_id", help="fan id")
    fan_off_parser.add_argument("--config", type=Path, required=True, help="センサー設定 JSON のパス")

    fan_test_parser = subparsers.add_parser("fan:test", help="設定済みファンを順番にON/OFFします")
    fan_test_parser.add_argument("--config", type=Path, required=True, help="センサー設定 JSON のパス")
    fan_test_parser.add_argument("--sleep-seconds", type=float, default=3.0, help="各ファンをONにする秒数")

    return parser


def run_read(*, as_json: bool, config_path: Path | None) -> int:
    try:
        config = load_config(config_path) if config_path is not None else AppConfig(sensors={})
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    readings = read_all_configured_sensors(config=config)

    if as_json:
        payload = {"sensors": [configured_sensor_reading_to_dict(reading) for reading in readings]}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    for reading in readings:
        temperature = (
            f"{reading.temperature_c:.3f} C"
            if reading.temperature_c is not None
            else "- C"
        )
        print(f"{reading.name:<10}  {temperature:>9}  {reading.status:<7}  {reading.sensor_id}")

    return 0


def run_read_environment(*, as_json: bool, config_path: Path) -> int:
    config = _load_config(config_path)
    if config is None:
        return 1

    readings = read_all_environment_sensors(config)

    if as_json:
        payload = {"sensors": [_environment_reading_to_dict(reading) for reading in readings]}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    for reading in readings:
        if reading.temperature_c is None or reading.relative_humidity_percent is None:
            print(f"{reading.sensor_key}  error={reading.error}")
            continue
        print(
            f"{reading.sensor_key}  "
            f"temperature={reading.temperature_c:.2f}C "
            f"humidity={reading.relative_humidity_percent:.2f}%"
        )

    return 0


def run_read_leak(*, as_json: bool, config_path: Path) -> int:
    config = _load_config(config_path)
    if config is None:
        return 1

    readings = read_all_leak_sensors(config)

    if as_json:
        payload = {"sensors": [leak_reading_to_dict(reading) for reading in readings]}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    for reading in readings:
        raw = "-" if reading.raw_value is None else str(reading.raw_value)
        if reading.error is None:
            print(f"{reading.sensor_key} status={reading.status} raw={raw}")
        else:
            print(f"{reading.sensor_key} status={reading.status} raw={raw} error={reading.error}")

    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "read":
        return run_read(as_json=args.json, config_path=args.config)
    if args.command == "read-environment":
        return run_read_environment(as_json=args.json, config_path=args.config)
    if args.command == "read-leak":
        return run_read_leak(as_json=args.json, config_path=args.config)
    if args.command == "serve":
        return run_serve(config_path=args.config, host=args.host, port=args.port)
    if args.command == "log-once":
        return run_log_once(config_path=args.config)
    if args.command == "collect":
        return run_collect(config_path=args.config)
    if args.command == "db-init":
        return run_db_init(config_path=args.config)
    if args.command == "db-stats":
        return run_db_stats(config_path=args.config)
    if args.command == "fetch-weather-once":
        return run_fetch_weather_once(config_path=args.config)
    if args.command == "weather-collect":
        return run_weather_collect(config_path=args.config)
    if args.command == "fan:list":
        return run_fan_list(config_path=args.config, as_json=args.json)
    if args.command == "fan:on":
        return run_fan_on(config_path=args.config, fan_id=args.fan_id)
    if args.command == "fan:off":
        return run_fan_off(config_path=args.config, fan_id=args.fan_id)
    if args.command == "fan:test":
        return run_fan_test_command(config_path=args.config, sleep_seconds=args.sleep_seconds)

    raise AssertionError(f"unsupported command: {args.command}")


def run_serve(*, config_path: Path, host: str | None, port: int | None) -> int:
    try:
        config = load_config(config_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    listen_host = host if host is not None else config.listen_addr
    listen_port = port if port is not None else config.listen_port
    print(f"serving aquapi on {listen_host}:{listen_port}", file=sys.stderr)

    try:
        serve_api(config, host=host, port=port)
    except (OSError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return 0


def run_log_once(*, config_path: Path) -> int:
    config = _load_config_for_logging(config_path)
    if config is None:
        return 1

    try:
        result = log_once(config)
    except (OSError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    sensors = result.entry.get("sensors")
    saved_count = len(sensors) if isinstance(sensors, list) else result.entry.get("saved_count", 0)
    print(f"Saved {saved_count} readings to {result.path}")
    return 0


def run_collect(*, config_path: Path) -> int:
    config = _load_config_for_logging(config_path)
    if config is None:
        return 1

    try:
        collect_forever(config)
    except KeyboardInterrupt:
        return 0
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return 0


def run_fan_list(*, config_path: Path, as_json: bool) -> int:
    config = _load_config(config_path)
    if config is None:
        return 1

    states = FanStateStore(default_fan_state_path(config)).load(config)
    payload = {
        "fans": [
            {
                "id": fan.fan_id,
                "name": fan.name,
                "gpio": fan.gpio,
                "active_high": fan.active_high,
                "enabled": fan.enabled,
                "state": fan_state_to_dict(states[fan.fan_id]) if fan.fan_id in states else None,
            }
            for fan in sorted(config.configured_fans().values(), key=lambda item: item.fan_id)
        ]
    }
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    for fan in payload["fans"]:
        assert isinstance(fan, dict)
        state = fan.get("state")
        state_text = "-"
        reason_text = "-"
        if isinstance(state, dict):
            state_text = str(state.get("state", "-"))
            reason_text = str(state.get("reason", "-"))
        print(f"{fan['id']} gpio={fan['gpio']} enabled={fan['enabled']} state={state_text} reason={reason_text}")
    return 0


def run_fan_on(*, config_path: Path, fan_id: str) -> int:
    config = _load_config(config_path)
    if config is None:
        return 1
    return set_manual_fan_state(config, fan_id, FAN_ON)


def run_fan_off(*, config_path: Path, fan_id: str) -> int:
    config = _load_config(config_path)
    if config is None:
        return 1
    return set_manual_fan_state(config, fan_id, FAN_OFF)


def run_fan_test_command(*, config_path: Path, sleep_seconds: float) -> int:
    config = _load_config(config_path)
    if config is None:
        return 1
    return run_fan_test(config, sleep_seconds=sleep_seconds)


def run_db_init(*, config_path: Path) -> int:
    config = _load_config(config_path)
    if config is None:
        return 1
    if config.logging.storage != "sqlite":
        print("error: db-init requires logging.storage sqlite", file=sys.stderr)
        return 1

    try:
        initialize_storage(config)
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Initialized {config.logging.database_path}")
    return 0


def run_db_stats(*, config_path: Path) -> int:
    config = _load_config(config_path)
    if config is None:
        return 1
    if config.logging.storage != "sqlite":
        print("error: db-stats requires logging.storage sqlite", file=sys.stderr)
        return 1

    try:
        stats = SQLiteStorage(config.logging.database_path).stats()
    except sqlite3.Error as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Database: {stats.path}")
    print(f"Readings: {stats.readings_count}")
    print(f"First: {_format_ts(stats.first_ts)}")
    print(f"Last:  {_format_ts(stats.last_ts)}")
    print(f"Sensors: {stats.sensors_count}")
    return 0


def run_fetch_weather_once(*, config_path: Path) -> int:
    config = _load_config_for_weather(config_path)
    if config is None:
        return 1

    try:
        result = fetch_weather_once(config)
    except (OSError, sqlite3.Error, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Saved {result.saved_count} hourly weather records to {result.path}")
    return 0


def run_weather_collect(*, config_path: Path) -> int:
    config = _load_config_for_weather(config_path)
    if config is None:
        return 1

    try:
        collect_weather_forever(config)
    except KeyboardInterrupt:
        return 0
    except (OSError, sqlite3.Error, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return 0


def _load_config_for_logging(config_path: Path) -> AppConfig | None:
    config = _load_config(config_path)
    if config is None:
        return None

    if not config.logging.enabled:
        print("error: logging is disabled", file=sys.stderr)
        return None

    return config


def _load_config_for_weather(config_path: Path) -> AppConfig | None:
    config = _load_config(config_path)
    if config is None:
        return None
    if not config.weather.enabled:
        print("error: weather is disabled", file=sys.stderr)
        return None
    if config.weather.source != "open-meteo":
        print("error: only open-meteo weather source is supported", file=sys.stderr)
        return None
    if config.logging.storage != "sqlite":
        print("error: weather storage requires logging.storage sqlite", file=sys.stderr)
        return None
    return config


def _load_config(config_path: Path) -> AppConfig | None:
    try:
        config = load_config(config_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return None

    return config


def _format_ts(ts: int | None) -> str:
    if ts is None:
        return "-"
    from datetime import datetime

    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def _environment_reading_to_dict(reading: EnvironmentReading) -> dict[str, object]:
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
        "temperature_c": reading.temperature_c,
        "relative_humidity_percent": reading.relative_humidity_percent,
        "crc_ok": reading.crc_ok,
        "error": reading.error,
    }


if __name__ == "__main__":
    raise SystemExit(main())
