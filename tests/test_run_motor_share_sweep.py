import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from run_motor_share_sweep import (
    RunSpec,
    bus_angle_frequency_deviation_hz,
    build_specs,
    detect_transmission_interface_bus,
    find_free_base_port,
    format_share_label,
    format_share_percent_label,
    load_distribution_series,
    omega_pu_to_hz,
    parse_andes_lst_omega_columns,
    parse_shares,
)


class MotorShareSweepTest(unittest.TestCase):
    def test_step_includes_one(self):
        shares = parse_shares(Namespace(step=0.25, shares=""))

        self.assertEqual(shares, [0.0, 0.25, 0.5, 0.75, 1.0])

    def test_explicit_shares_are_deduplicated_and_labelled(self):
        shares = parse_shares(Namespace(step=None, shares="0, 0.5 0.5 1"))

        self.assertEqual(shares, [0.0, 0.5, 1.0])
        self.assertEqual(format_share_label(0.5), "share_0p5")
        self.assertEqual(format_share_percent_label(0.75), "75%")
        self.assertEqual(format_share_percent_label(1.0), "100%")
        self.assertEqual(omega_pu_to_hz(1.001, 60.0), 60.059999999999995)

    def test_ports_are_spaced_by_stride(self):
        args = Namespace(output_dir=Path("/tmp/sweep"), base_port=25000, port_stride=10)

        specs = build_specs(args, [0.0, 0.25, 0.5])

        self.assertEqual([spec.port for spec in specs], [25000, 25010, 25020])

    def test_auto_base_port_finds_free_block(self):
        busy_ports = {25000, 25010, 25030}

        base_port = find_free_base_port(
            base_port=25000,
            port_stride=10,
            run_count=2,
            port_is_occupied=lambda port: port in busy_ports,
        )

        self.assertEqual(base_port, 25040)

    def test_failed_feeder_log_does_not_break_summary(self):
        with TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            (run_dir / "feeder_1.log").write_text(
                "HELICS connection failed before data rows\n",
                encoding="utf-8",
            )
            spec = RunSpec(share=0.25, label="share_0p25", port=25001, run_dir=run_dir)

            with redirect_stdout(StringIO()):
                self.assertIsNone(load_distribution_series(spec))

    def test_detects_transmission_interface_bus_from_logs(self):
        with TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            (run_dir / "transmission.log").write_text(
                "Transmission config: interface_bus=14 feeders=1\n",
                encoding="utf-8",
            )
            spec = RunSpec(share=1.0, label="share_1", port=25000, run_dir=run_dir)

            self.assertEqual(detect_transmission_interface_bus([spec]), "14")

    def test_bus_angle_frequency_deviation_uses_unwrapped_derivative(self):
        freq = bus_angle_frequency_deviation_hz(
            time_s=pd.Series([0.0, 0.5, 1.0]),
            angle_rad=pd.Series([0.0, 0.2 * 3.141592653589793, 0.4 * 3.141592653589793]),
        )

        self.assertEqual(len(freq), 3)
        self.assertAlmostEqual(freq["frequency_deviation_hz"].iloc[1], 0.2)

    def test_parses_andes_lst_omega_columns(self):
        with TemporaryDirectory() as temp_dir:
            lst_path = Path(temp_dir) / "case_out.lst"
            lst_path.write_text(
                "     6,            omega GENROU 1,                   $\\omega$ GENROU 1\n"
                "     7,            omega GENROU 2,                   $\\omega$ GENROU 2\n",
                encoding="utf-8",
            )

            self.assertEqual(
                parse_andes_lst_omega_columns(lst_path),
                {"GENROU_1": 6, "GENROU_2": 7},
            )


if __name__ == "__main__":
    unittest.main()
