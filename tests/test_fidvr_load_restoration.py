import unittest

from fidvr_load_restoration import (
    ThermalLoadRestorationConfig,
    ThermalLoadRestorationState,
    update_thermal_load_restoration,
)


class ThermalLoadRestorationTest(unittest.TestCase):
    def test_disabled_restoration_remains_zero(self):
        state = update_thermal_load_restoration(
            ThermalLoadRestorationState(),
            ThermalLoadRestorationConfig(enabled=False),
            thermal_trip_fraction=0.8,
            voltage_pu=1.0,
            current_time_s=300.0,
        )

        self.assertEqual(state.restored_fraction, 0.0)
        self.assertIsNone(state.trip_time_s)

    def test_waits_for_delay_and_voltage_before_ramping(self):
        config = ThermalLoadRestorationConfig(
            enabled=True,
            restore_delay_s=180.0,
            restore_ramp_s=30.0,
            restore_voltage_pu=0.90,
            restore_fraction=1.0,
        )
        state = ThermalLoadRestorationState()

        state = update_thermal_load_restoration(
            state,
            config,
            thermal_trip_fraction=0.8,
            voltage_pu=1.0,
            current_time_s=10.0,
        )
        self.assertEqual(state.trip_time_s, 10.0)
        self.assertEqual(state.restored_fraction, 0.0)

        state = update_thermal_load_restoration(
            state,
            config,
            thermal_trip_fraction=0.8,
            voltage_pu=1.0,
            current_time_s=189.0,
        )
        self.assertEqual(state.restored_fraction, 0.0)

        state = update_thermal_load_restoration(
            state,
            config,
            thermal_trip_fraction=0.8,
            voltage_pu=1.0,
            current_time_s=190.0,
        )
        self.assertEqual(state.restore_started_at_s, 190.0)
        self.assertEqual(state.restored_fraction, 0.0)

        state = update_thermal_load_restoration(
            state,
            config,
            thermal_trip_fraction=0.8,
            voltage_pu=1.0,
            current_time_s=205.0,
        )
        self.assertAlmostEqual(state.restored_fraction, 0.4)

    def test_restore_fraction_limits_returning_load(self):
        state = update_thermal_load_restoration(
            ThermalLoadRestorationState(),
            ThermalLoadRestorationConfig(
                enabled=True,
                restore_delay_s=0.0,
                restore_ramp_s=0.0,
                restore_fraction=0.5,
            ),
            thermal_trip_fraction=0.8,
            voltage_pu=1.0,
            current_time_s=10.0,
        )

        self.assertAlmostEqual(state.restored_fraction, 0.4)
        self.assertAlmostEqual(state.target_fraction, 0.4)

    def test_voltage_dropout_resets_restored_fraction(self):
        config = ThermalLoadRestorationConfig(
            enabled=True,
            restore_delay_s=0.0,
            restore_ramp_s=0.0,
            dropout_voltage_pu=0.70,
        )
        state = update_thermal_load_restoration(
            ThermalLoadRestorationState(),
            config,
            thermal_trip_fraction=0.8,
            voltage_pu=1.0,
            current_time_s=10.0,
        )
        self.assertGreater(state.restored_fraction, 0.0)

        state = update_thermal_load_restoration(
            state,
            config,
            thermal_trip_fraction=0.8,
            voltage_pu=0.65,
            current_time_s=20.0,
        )

        self.assertEqual(state.restored_fraction, 0.0)
        self.assertEqual(state.trip_time_s, 20.0)


if __name__ == "__main__":
    unittest.main()
