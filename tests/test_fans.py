import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from aquapi.config import AppConfig, FanConfig, FanControlConfig, SensorConfig, TemperatureAlertConfig
from aquapi.fans import (
    FAN_MODE_AUTO,
    FAN_MODE_MANUAL_OFF,
    FAN_MODE_MANUAL_ON,
    FAN_OFF,
    FAN_ON,
    FanState,
    FanStateStore,
    build_fan_states,
    set_fan_mode,
    temperature_alert_state,
)
from aquapi.sensors import ConfiguredSensorReading


NOW = datetime(2026, 6, 19, 12, 0, tzinfo=timezone.utc)


class FanTests(unittest.TestCase):
    def test_temperature_alert_state_uses_configured_thresholds(self) -> None:
        self.assertEqual(temperature_alert_state(make_reading(temperature_c=29.0)), "ok")
        self.assertEqual(temperature_alert_state(make_reading(temperature_c=30.0)), "hot")
        self.assertEqual(temperature_alert_state(make_reading(temperature_c=15.0)), "cold")
        self.assertEqual(temperature_alert_state(make_reading(temperature_c=None, crc_ok=False)), "unknown")
        self.assertEqual(
            temperature_alert_state(
                make_reading(
                    temperature_c=30.0,
                    temperature_alert=TemperatureAlertConfig(enabled=False),
                )
            ),
            "disabled",
        )

    def test_build_fan_states_turns_on_above_start_and_off_below_stop(self) -> None:
        config = make_config()

        hot = build_fan_states(config, [make_reading(temperature_c=28.0)], now=NOW)[0]
        cold = build_fan_states(config, [make_reading(temperature_c=27.5)], now=NOW)[0]

        self.assertEqual(hot.state, FAN_ON)
        self.assertEqual(hot.reason, "temperature_above_start")
        self.assertEqual(cold.state, FAN_OFF)
        self.assertEqual(cold.reason, "temperature_below_stop")

    def test_build_fan_states_keeps_previous_state_inside_hysteresis(self) -> None:
        config = make_config()
        previous_on = {
            "fan_1": FanState(
                fan_id="fan_1",
                name="Fan 1",
                gpio=22,
                active_high=True,
                enabled=True,
                state=FAN_ON,
                bound_tank_id="28-1",
                reason="temperature_above_start",
            )
        }

        keep_on = build_fan_states(
            config,
            [make_reading(temperature_c=27.8)],
            previous_states=previous_on,
            now=NOW,
        )[0]
        keep_off = build_fan_states(config, [make_reading(temperature_c=27.8)], now=NOW)[0]

        self.assertEqual(keep_on.state, FAN_ON)
        self.assertEqual(keep_on.reason, "within_hysteresis_keep_on")
        self.assertEqual(keep_off.state, FAN_OFF)
        self.assertEqual(keep_off.reason, "within_hysteresis_keep_off")

    def test_build_fan_states_does_not_keep_manual_on_inside_hysteresis(self) -> None:
        config = make_config()
        previous_manual_on = {
            "fan_1": FanState(
                fan_id="fan_1",
                name="Fan 1",
                gpio=22,
                active_high=True,
                enabled=True,
                state=FAN_ON,
                bound_tank_id="28-1",
                reason="manual_on",
            )
        }

        state = build_fan_states(
            config,
            [make_reading(temperature_c=27.8)],
            previous_states=previous_manual_on,
            now=NOW,
        )[0]

        self.assertEqual(state.state, FAN_OFF)
        self.assertEqual(state.reason, "within_hysteresis_keep_off")

    def test_build_fan_states_keeps_manual_on_mode_regardless_of_temperature(self) -> None:
        config = make_config()
        previous_manual_on = {
            "fan_1": FanState(
                fan_id="fan_1",
                name="Fan 1",
                gpio=22,
                active_high=True,
                enabled=True,
                state=FAN_ON,
                bound_tank_id="28-1",
                reason="manual_on",
                mode=FAN_MODE_MANUAL_ON,
            )
        }

        state = build_fan_states(
            config,
            [make_reading(temperature_c=20.0)],
            previous_states=previous_manual_on,
            now=NOW,
        )[0]

        self.assertEqual(state.state, FAN_ON)
        self.assertEqual(state.mode, FAN_MODE_MANUAL_ON)
        self.assertEqual(state.reason, "manual_on")

    def test_build_fan_states_keeps_manual_off_mode_regardless_of_temperature(self) -> None:
        config = make_config()
        previous_manual_off = {
            "fan_1": FanState(
                fan_id="fan_1",
                name="Fan 1",
                gpio=22,
                active_high=True,
                enabled=True,
                state=FAN_OFF,
                bound_tank_id="28-1",
                reason="manual_off",
                mode=FAN_MODE_MANUAL_OFF,
            )
        }

        state = build_fan_states(
            config,
            [make_reading(temperature_c=30.0)],
            previous_states=previous_manual_off,
            now=NOW,
        )[0]

        self.assertEqual(state.state, FAN_OFF)
        self.assertEqual(state.mode, FAN_MODE_MANUAL_OFF)
        self.assertEqual(state.reason, "manual_off")

    def test_set_fan_mode_auto_resumes_temperature_control(self) -> None:
        config = make_config()
        with TemporaryDirectory() as tmp_dir:
            store = FanStateStore(Path(tmp_dir) / "fan-state.json")
            set_fan_mode(
                config,
                "fan_1",
                FAN_MODE_MANUAL_ON,
                [make_reading(temperature_c=20.0)],
                state_store=store,
                now=NOW,
            )
            result = set_fan_mode(
                config,
                "fan_1",
                FAN_MODE_AUTO,
                [make_reading(temperature_c=27.5)],
                state_store=store,
                now=NOW,
            )

        self.assertEqual(result.current.mode, FAN_MODE_AUTO)
        self.assertEqual(result.current.state, FAN_OFF)
        self.assertEqual(result.current.reason, "temperature_below_stop")

    def test_state_store_infers_legacy_manual_reason_as_mode(self) -> None:
        config = make_config()
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "fan-state.json"
            path.write_text(
                json.dumps(
                    {
                        "fans": [
                            {
                                "id": "fan_1",
                                "state": "on",
                                "reason": "manual_on",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            state = FanStateStore(path).load(config)["fan_1"]

        self.assertEqual(state.mode, FAN_MODE_MANUAL_ON)

    def test_build_fan_states_turns_off_when_temperature_unknown(self) -> None:
        state = build_fan_states(make_config(), [make_reading(temperature_c=None, crc_ok=False)], now=NOW)[0]

        self.assertEqual(state.state, FAN_OFF)
        self.assertEqual(state.reason, "temperature_unknown")

    def test_build_fan_states_disables_disabled_fan(self) -> None:
        config = make_config(fan_enabled=False)

        state = build_fan_states(config, [make_reading(temperature_c=30.0)], now=NOW)[0]

        self.assertEqual(state.state, "disabled")
        self.assertEqual(state.reason, "fan_disabled")


def make_config(*, fan_enabled: bool = True) -> AppConfig:
    return AppConfig(
        fans={
            "fan_1": FanConfig(
                fan_id="fan_1",
                name="Fan 1",
                gpio=22,
                active_high=True,
                enabled=fan_enabled,
            )
        },
        sensors={
            "28-1": SensorConfig(
                sensor_id="28-1",
                name="水槽",
                type="water",
                offset=0.0,
                min=18.0,
                max=28.0,
                temperature_alert=TemperatureAlertConfig(
                    enabled=True,
                    too_hot_c=30.0,
                    too_cold_c=15.0,
                ),
                fan_control=FanControlConfig(
                    enabled=True,
                    fan_id="fan_1",
                    start_c=28.0,
                    stop_c=27.5,
                ),
            )
        },
    )


def make_reading(
    *,
    temperature_c: float | None,
    crc_ok: bool = True,
    temperature_alert: TemperatureAlertConfig | None = None,
) -> ConfiguredSensorReading:
    return ConfiguredSensorReading(
        sensor_id="28-1",
        name="水槽",
        type="water",
        raw_temperature_c=temperature_c,
        temperature_c=temperature_c,
        offset=0.0,
        min=18.0,
        max=28.0,
        status="ok" if crc_ok else "error",
        crc_ok=crc_ok,
        raw="raw",
        error=None if crc_ok else "CRC チェックが失敗しました",
        role="aquarium",
        enabled=True,
        visible=True,
        sort_order=10,
        temperature_alert=temperature_alert
        or TemperatureAlertConfig(enabled=True, too_hot_c=30.0, too_cold_c=15.0),
        fan_control=FanControlConfig(enabled=True, fan_id="fan_1", start_c=28.0, stop_c=27.5),
    )
