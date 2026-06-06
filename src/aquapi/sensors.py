from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


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

