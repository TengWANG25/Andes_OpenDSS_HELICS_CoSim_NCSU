import unittest

from fidvr_controls import (
    DelayedShuntControlConfig,
    DelayedShuntControlState,
    shunt_status_from_fraction,
    update_delayed_shunt_control,
)


class DelayedShuntControlTest(unittest.TestCase):
    def test_names_partial_shunt_fleet_status(self):
        self.assertEqual(shunt_status_from_fraction(1.0), "on")
        self.assertEqual(shunt_status_from_fraction(0.5), "partial")
        self.assertEqual(shunt_status_from_fraction(0.0), "off")

    def test_switches_off_only_after_sustained_overvoltage(self):
        config = DelayedShuntControlConfig(
            on_voltage_pu=0.97,
            off_voltage_pu=1.03,
            on_delay_s=10.0,
            off_delay_s=2.0,
        )
        state = DelayedShuntControlState(enabled=True)

        state = update_delayed_shunt_control(
            state, config, monitored_voltage_pu=1.04, current_time_s=5.0
        )
        self.assertTrue(state.enabled)
        self.assertEqual(state.off_armed_since_s, 5.0)

        state = update_delayed_shunt_control(
            state, config, monitored_voltage_pu=1.04, current_time_s=6.9
        )
        self.assertTrue(state.enabled)

        state = update_delayed_shunt_control(
            state, config, monitored_voltage_pu=1.04, current_time_s=7.0
        )
        self.assertFalse(state.enabled)
        self.assertEqual(state.last_action, "opened")

    def test_lockout_keeps_capacitor_off_after_overvoltage_trip(self):
        config = DelayedShuntControlConfig(
            on_voltage_pu=0.97,
            off_voltage_pu=1.03,
            on_delay_s=10.0,
            off_delay_s=2.0,
            lockout_after_open=True,
        )
        state = DelayedShuntControlState(enabled=True)

        state = update_delayed_shunt_control(
            state, config, monitored_voltage_pu=1.04, current_time_s=0.0
        )
        state = update_delayed_shunt_control(
            state, config, monitored_voltage_pu=1.04, current_time_s=2.0
        )
        self.assertFalse(state.enabled)
        self.assertTrue(state.locked_out)

        state = update_delayed_shunt_control(
            state, config, monitored_voltage_pu=0.94, current_time_s=30.0
        )
        self.assertFalse(state.enabled)
        self.assertIsNone(state.on_armed_since_s)


if __name__ == "__main__":
    unittest.main()
