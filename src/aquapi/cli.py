from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from aquapi.config import AppConfig, load_config
from aquapi.sensors import ConfiguredSensorReading, read_all_configured_sensors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aquapi")
    subparsers = parser.add_subparsers(dest="command", required=True)

    read_parser = subparsers.add_parser("read", help="1-Wire 温度センサーを読み取ります")
    read_parser.add_argument("--json", action="store_true", help="JSON 形式で出力します")
    read_parser.add_argument("--config", type=Path, help="センサー設定 JSON のパス")

    return parser


def run_read(*, as_json: bool, config_path: Path | None) -> int:
    try:
        config = load_config(config_path) if config_path is not None else AppConfig(sensors={})
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    readings = read_all_configured_sensors(config=config)

    if as_json:
        payload = {"sensors": [_reading_to_json(reading) for reading in readings]}
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

    raise AssertionError(f"unsupported command: {args.command}")


def _reading_to_json(reading: ConfiguredSensorReading) -> dict[str, object]:
    payload: dict[str, object] = {
        "sensor_id": reading.sensor_id,
        "name": reading.name,
        "type": reading.type,
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


if __name__ == "__main__":
    raise SystemExit(main())
