from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from aquapi.api import serve_api
from aquapi.config import AppConfig, load_config
from aquapi.logs import collect_forever, initialize_storage, log_once
from aquapi.sqlite_storage import SQLiteStorage
from aquapi.sensors import (
    configured_sensor_reading_to_dict,
    read_all_configured_sensors,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aquapi")
    subparsers = parser.add_subparsers(dest="command", required=True)

    read_parser = subparsers.add_parser("read", help="1-Wire 温度センサーを読み取ります")
    read_parser.add_argument("--json", action="store_true", help="JSON 形式で出力します")
    read_parser.add_argument("--config", type=Path, help="センサー設定 JSON のパス")

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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "read":
        return run_read(as_json=args.json, config_path=args.config)
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


def _load_config_for_logging(config_path: Path) -> AppConfig | None:
    config = _load_config(config_path)
    if config is None:
        return None

    if not config.logging.enabled:
        print("error: logging is disabled", file=sys.stderr)
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


if __name__ == "__main__":
    raise SystemExit(main())
