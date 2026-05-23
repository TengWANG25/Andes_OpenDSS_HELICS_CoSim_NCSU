import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from plot_distribution_all_bus_voltages import (
    load_all_bus_voltage_series,
    parse_bus_filter,
    plot_distribution_all_bus_voltages,
)


class DistributionAllBusVoltagePlotTest(unittest.TestCase):
    def test_plots_selected_bus_voltage_series(self):
        with TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            csv_path = run_dir / "feeder_1_all_bus_voltages.csv"
            csv_path.write_text(
                "feeder,iter,t_granted,state,bus,node_count,vavg_pu,vpos_pu,"
                "vneg_pu,vzero_pu,va_pu,vb_pu,vc_pu,anga_deg,angb_deg,angc_deg\n"
                "1,0,0.0,INITIAL,650,3,1.000,1.000,0,0,1.0,1.0,1.0,0,-120,120\n"
                "1,1,1.0,NEXT_STEP,650,3,0.900,0.900,0,0,0.9,0.9,0.9,0,-120,120\n"
                "1,0,0.0,INITIAL,632,3,0.990,0.990,0,0,0.99,0.99,0.99,0,-120,120\n"
                "1,1,1.0,NEXT_STEP,632,3,0.880,0.880,0,0,0.88,0.88,0.88,0,-120,120\n",
                encoding="utf-8",
            )
            data = load_all_bus_voltage_series(run_dir, [])
            scope, buses = parse_bus_filter("650,632")
            outputs = plot_distribution_all_bus_voltages(
                data,
                run_dir / "plots",
                bus_scope=scope,
                bus_order=buses,
                feeder=1,
            )

            output_csv, output_plot = outputs[0]
            plotted = pd.read_csv(output_csv)
            plot_exists = output_plot.exists()

        self.assertTrue(plot_exists)
        self.assertEqual(set(plotted["node"].astype(str)), {"650", "632"})
        node650 = plotted.loc[plotted["node"].astype(str) == "650", "plot_v_pu"].tolist()
        self.assertEqual(node650, [1.0, 0.9])


if __name__ == "__main__":
    unittest.main()
