import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from plot_feeder_substation_voltages import load_all_feeder_substation_series


def feeder_row(feeder: int, iteration: int, time_s: float, source_v: float) -> str:
    return (
        f"[Feeder{feeder:02d}] iter={iteration:06d} "
        f"t_granted={time_s:.3f}s (t_req={time_s:.3f}s, dt=0.003s) "
        f"state=NEXT_STEP | Vupdate=True V={source_v:.6f} pu "
        "ang=-10.000000 deg | DistBus=675 Vavg=0.990000 pu "
        "Va=0.980000 pu Vb=1.000000 pu Vc=0.990000 pu Vpos=0.990000 pu "
        "AlertSignal=dist_bus AlertBus=675 AlertV=0.990000 pu "
        "AlertVpos=0.990000 pu AlertVavg=0.990000 pu | "
        "TotalPower=-1000.00 kW, -500.00 kvar | Pub=0.010000+j0.005000 pu "
        "LoadMult=1.0000 | FIDVR=BASELINE TxV=1.000000 MotorP=1.000 "
        "MotorQ=1.000 Caps=on CapFrac=1.000 Tap=1.00000 Restore=1.000 "
        "ThermalRestore=0.000 SlipAvg=nan SlipMax=nan MotorPF=nan "
        "Running=12 Stalled=0 Tripped=0 Restoring=0\n"
    )


class FeederSubstationVoltagePlotTest(unittest.TestCase):
    def test_loads_all_feeder_source_voltage_series(self):
        with TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            (run_dir / "feeder_1.log").write_text(
                feeder_row(1, 1, 0.0, 1.01) + feeder_row(1, 2, 0.003, 0.99),
                encoding="utf-8",
            )
            (run_dir / "feeder_2.log").write_text(
                feeder_row(2, 1, 0.0, 1.02) + feeder_row(2, 2, 0.003, 0.98),
                encoding="utf-8",
            )

            data = load_all_feeder_substation_series(run_dir, [])

        self.assertEqual(sorted(data["feeder"].unique()), ["Feeder 1", "Feeder 2"])
        self.assertEqual(len(data), 4)
        self.assertEqual(
            data.loc[data["feeder"] == "Feeder 2", "source_v_pu"].tolist(),
            [1.02, 0.98],
        )


if __name__ == "__main__":
    unittest.main()
