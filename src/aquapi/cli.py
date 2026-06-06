from __future__ import annotations

import argparse
import json
import sys

from aquapi.sensors import SensorReadError, read_all_sensors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aquapi")
    subparsers = parser.add_subparsers(dest="command", required=True)

    read_parser = subparsers.add_parser("read", help="1-Wire 温度センサーを読み取ります")
    read_parser.add_argument("--json", action="store_true", help="JSON 形式で出力します")

    return parser


def run_read(*, as_json: bool) -> int:
    try:
        readings = read_all_sensors()
    except (OSError, SensorReadError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if as_json:
        payload = {
            "sensors": [
                {
                    "sensor_id": reading.sensor_id,
                    "temperature_c": reading.temperature_c,
                    "crc_ok": reading.crc_ok,
                }
                for reading in readings
            ]
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    for reading in readings:
        print(f"{reading.sensor_id}  {reading.temperature_c:.3f} C")

    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "read":
        return run_read(as_json=args.json)

    raise AssertionError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())

