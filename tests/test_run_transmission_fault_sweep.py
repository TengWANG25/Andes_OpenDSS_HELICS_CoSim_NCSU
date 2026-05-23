import unittest
from argparse import Namespace
from pathlib import Path
from tempfile import TemporaryDirectory

from run_transmission_fault_sweep import (
    build_specs,
    parse_bus_list,
    parse_line_list,
    read_scenarios,
    sanitize_name,
    scenario_legend_label,
)


class TransmissionFaultSweepTest(unittest.TestCase):
    def test_parse_bus_list_deduplicates(self):
        self.assertEqual(parse_bus_list("14, 13 14,9"), [14, 13, 9])

    def test_parse_line_list_deduplicates(self):
        self.assertEqual(parse_line_list("Line_13, Line_16 Line_13"), ["Line_13", "Line_16"])

    def test_generated_scenarios_from_fault_buses(self):
        args = Namespace(
            scenarios=None,
            fault_buses="14,13",
            line_trips="",
            fault_time=1.0,
            fault_duration=0.08,
            line_trip_time=None,
            line_trip_duration=None,
            fault_rf=0.0,
            fault_xf=0.3,
            target_time=10.0,
            only=None,
        )

        scenarios = read_scenarios(args)

        self.assertEqual([scenario.fault_bus for scenario in scenarios], [14, 13])
        self.assertEqual(scenarios[0].fault_time_s, 1.0)
        self.assertEqual(scenarios[0].fault_duration_s, 0.08)
        self.assertEqual(scenarios[0].target_time_s, 10.0)
        self.assertEqual(scenarios[0].kind, "bus_fault")

    def test_generated_line_trip_scenarios(self):
        args = Namespace(
            scenarios=None,
            fault_buses="",
            line_trips="Line_13,Line_16",
            fault_time=1.0,
            fault_duration=0.08,
            line_trip_time=1.2,
            line_trip_duration=0.1,
            fault_rf=0.0,
            fault_xf=0.3,
            target_time=10.0,
            only=None,
        )

        scenarios = read_scenarios(args)

        self.assertEqual([scenario.kind for scenario in scenarios], ["line_trip", "line_trip"])
        self.assertEqual([scenario.line_idx for scenario in scenarios], ["Line_13", "Line_16"])
        self.assertEqual(scenarios[0].fault_time_s, 1.2)
        self.assertEqual(scenarios[0].fault_duration_s, 0.1)

    def test_reads_bus_fault_csv(self):
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "faults.csv"
            path.write_text(
                "enabled,name,kind,bus,line_idx,start_time,clear_time,xf,rf,target_time,notes\n"
                "1,NearInterface,bus_fault,14,,1.0,1.08,0.3,0.0,10.0,test\n"
                "0,Disabled,bus_fault,13,,1.0,1.08,0.3,0.0,10.0,test\n",
                encoding="utf-8",
            )
            args = Namespace(
                scenarios=path,
                only=None,
                target_time=None,
                fault_buses="",
                line_trips="",
                fault_time=1.0,
                fault_duration=0.08,
                line_trip_time=None,
                line_trip_duration=None,
                fault_rf=0.0,
                fault_xf=0.3,
            )

            scenarios = read_scenarios(args)

            self.assertEqual(len(scenarios), 1)
            self.assertEqual(scenarios[0].name, "NearInterface")
            self.assertEqual(scenarios[0].fault_bus, 14)
            self.assertAlmostEqual(scenarios[0].fault_duration_s, 0.08)

    def test_reads_line_trip_csv(self):
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "faults.csv"
            path.write_text(
                "enabled,name,kind,bus,line_idx,start_time,clear_time,xf,rf,target_time,notes\n"
                "1,Line13Trip,line_trip,,Line_13,1.0,1.1,,,10.0,test\n",
                encoding="utf-8",
            )
            args = Namespace(
                scenarios=path,
                only=None,
                target_time=None,
                fault_buses="",
                line_trips="",
                fault_time=1.0,
                fault_duration=0.08,
                line_trip_time=None,
                line_trip_duration=None,
                fault_rf=0.0,
                fault_xf=0.3,
            )

            scenarios = read_scenarios(args)

            self.assertEqual(scenarios[0].kind, "line_trip")
            self.assertEqual(scenarios[0].line_idx, "Line_13")
            self.assertIsNone(scenarios[0].fault_bus)

    def test_build_specs_uses_scenario_labels_and_ports(self):
        args = Namespace(output_dir=Path("/tmp/faults"), base_port=26000, port_stride=10)
        scenarios = read_scenarios(
            Namespace(
                scenarios=None,
                fault_buses="14,13",
                line_trips="",
                fault_time=1.0,
                fault_duration=0.08,
                line_trip_time=None,
                line_trip_duration=None,
                fault_rf=0.0,
                fault_xf=0.3,
                target_time=None,
                only=None,
            )
        )

        specs = build_specs(args, scenarios)

        self.assertEqual([spec.port for spec in specs], [26000, 26010])
        self.assertTrue(specs[0].run_dir.name.startswith("Bus14_fault"))

    def test_sanitize_name(self):
        self.assertEqual(sanitize_name("Bus 14 fault xf=0.3"), "Bus_14_fault_xf_0_3")

    def test_legend_omits_timing_and_impedance_details(self):
        scenarios = read_scenarios(
            Namespace(
                scenarios=None,
                fault_buses="14",
                line_trips="Line_13",
                fault_time=1.0,
                fault_duration=0.08,
                line_trip_time=None,
                line_trip_duration=None,
                fault_rf=0.0,
                fault_xf=0.3,
                target_time=None,
                only=None,
            )
        )
        specs = build_specs(
            Namespace(output_dir=Path("/tmp/faults"), base_port=26000, port_stride=10),
            scenarios,
        )

        self.assertEqual(scenario_legend_label(specs[0]), "Bus 14 fault")
        self.assertEqual(scenario_legend_label(specs[1]), "Line 13 trip")


if __name__ == "__main__":
    unittest.main()
