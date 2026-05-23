import unittest
from argparse import Namespace
from pathlib import Path

from run_fault_all_node_voltages import build_run_env


class RunFaultAllNodeVoltagesTest(unittest.TestCase):
    def test_bus_fault_env_enables_all_bus_logging(self):
        args = Namespace(
            event_kind="bus_fault",
            profile="weak_bus14",
            progress_interval="10",
            buses="ieee13",
            target_time=10.0,
            motor_share=0.75,
            fault_bus=14,
            fault_time=1.0,
            fault_duration=0.08,
            fault_rf=0.0,
            fault_xf=0.3,
            line="Line_13",
        )

        env = build_run_env(args, Path("/tmp/run"), 27000)

        self.assertEqual(env["TX_EVENT_KIND"], "bus_fault")
        self.assertEqual(env["TX_FAULT_BUS"], "14")
        self.assertEqual(env["DIST_LOG_ALL_BUS_VOLTAGES"], "1")
        self.assertEqual(env["DIST_ALL_BUS_VOLTAGE_BUSES"], "ieee13")
        self.assertEqual(env["FIDVR_MOTOR_SHARE"], "0.75")

    def test_line_trip_env_uses_line_trip_reclose(self):
        args = Namespace(
            event_kind="line_trip",
            profile="weak_bus14",
            progress_interval="10",
            buses="all",
            target_time=None,
            motor_share=None,
            fault_bus=14,
            fault_time=1.0,
            fault_duration=0.08,
            fault_rf=0.0,
            fault_xf=0.3,
            line="Line_16",
        )

        env = build_run_env(args, Path("/tmp/run"), 27001)

        self.assertEqual(env["TX_EVENT_KIND"], "line_trip_reclose")
        self.assertEqual(env["TX_LINE_TRIP_LINE"], "Line_16")
        self.assertEqual(env["TX_LINE_TRIP_DURATION"], "0.08")
        self.assertNotIn("FIDVR_MOTOR_SHARE", env)


if __name__ == "__main__":
    unittest.main()
