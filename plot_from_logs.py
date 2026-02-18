#!/usr/bin/env python3
"""
Parse transmission.log and plot:
  - Total P/Q vs time
  - Bus |V| vs time
  - Combined vertical subplots (shared x-axis)

Usage:
  python plot_power_voltage_vs_time.py --log transmission.log
  python plot_power_voltage_vs_time.py --log /path/to/transmission.log --bus 2 --out ./figs
"""

import re
import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # <-- ensures PNG saving works on headless systems

import pandas as pd
import matplotlib.pyplot as plt


def parse_transmission_log(log_path: Path, bus: int = 2) -> pd.DataFrame:
    text = log_path.read_text(errors="ignore").splitlines()

    # Header line example:
    # [iter=000001] t_granted=0.000s (t_req=300.000s, dt=300.000s) state=ITERATING
    re_header = re.compile(
        r"\[iter=(\d+)\]\s+t_granted=([0-9.+\-eE]+)s.*state=([A-Z_]+)"
    )

    # Total load example:
    # [iter=000001 t=0.000s] Total Distribution Load P=0.5959, Q=0.1023 (updated=1/10)
    re_total = re.compile(
        r"\[iter=(\d+)\s+t=([0-9.+\-eE]+)s\]\s*Total\s+Distribution\s+Load\s+"
        r"P=([\-0-9.+eE]+),\s*Q=([\-0-9.+eE]+)\s*\(updated=(\d+)/(\d+)\)"
    )

    # Bus voltage examples:
    # [iter=000001 t=0.000s] Bus2 |V|=0.939609, angle(rad)=-0.533586
    # [iter=000001 t=0.000s] Bus2 |V|=0.939609
    re_vmag_ang = re.compile(
        rf"\[iter=(\d+)\s+t=([0-9.+\-eE]+)s\]\s*Bus{bus}\s+\|V\|=([0-9.+\-eE]+),\s*angle\(rad\)=([\-0-9.+eE]+)"
    )
    re_vmag_only = re.compile(
        rf"\[iter=(\d+)\s+t=([0-9.+\-eE]+)s\]\s*Bus{bus}\s+\|V\|=([0-9.+\-eE]+)"
    )

    # We key by (iter, time) so we can merge header/total/vmag for the same iteration & time
    data = {}  # (iter, t) -> dict

    # Optional: remember latest t_granted per iter (in case some lines use t=... and header uses t_granted=...)
    it_to_tgranted = {}

    for line in text:
        m = re_header.search(line)
        if m:
            it = int(m.group(1))
            tgranted = float(m.group(2))
            st = m.group(3)
            it_to_tgranted[it] = tgranted
            key = (it, tgranted)
            data.setdefault(key, {})
            data[key].update({"iter": it, "t_granted": tgranted, "state": st})
            continue

        m = re_total.search(line)
        if m:
            it = int(m.group(1))
            t = float(m.group(2))
            P = float(m.group(3))
            Q = float(m.group(4))
            upd = int(m.group(5))
            tot = int(m.group(6))

            # Prefer t_granted if known; otherwise fall back to t
            tgranted = it_to_tgranted.get(it, t)
            key = (it, tgranted)
            data.setdefault(key, {})
            data[key].update(
                {"iter": it, "t_granted": tgranted, "P_total": P, "Q_total": Q,
                 "updated": upd, "n_feeders": tot}
            )
            continue

        m = re_vmag_ang.search(line)
        if m:
            it = int(m.group(1))
            t = float(m.group(2))
            Vmag = float(m.group(3))
            ang = float(m.group(4))

            tgranted = it_to_tgranted.get(it, t)
            key = (it, tgranted)
            data.setdefault(key, {})
            data[key].update({"iter": it, "t_granted": tgranted, "Vmag": Vmag, "Vang_rad": ang})
            continue

        m = re_vmag_only.search(line)
        if m:
            it = int(m.group(1))
            t = float(m.group(2))
            Vmag = float(m.group(3))

            tgranted = it_to_tgranted.get(it, t)
            key = (it, tgranted)
            data.setdefault(key, {})
            data[key].update({"iter": it, "t_granted": tgranted, "Vmag": Vmag})
            continue

    df = pd.DataFrame(list(data.values()))
    if df.empty:
        raise RuntimeError(
            "Parsed 0 rows. Your log format likely differs.\n"
            "Tip: search your log for lines containing 'Total Distribution Load' and 'Bus' and adjust regex."
        )

    df = df.sort_values(["t_granted", "iter"]).reset_index(drop=True)
    return df


def _time_axis_seconds_or_hours(t_seconds: pd.Series):
    # Choose hours if the span is >= 1 hour; else seconds
    if t_seconds.nunique() >= 2 and (t_seconds.max() - t_seconds.min()) >= 3600:
        return t_seconds / 3600.0, "Time (hours)"
    return t_seconds, "Time (s)"


def make_plots(df: pd.DataFrame, out_dir: Path, bus: int = 2):
    out_dir.mkdir(parents=True, exist_ok=True)

    # Take LAST iteration at each granted time (1 point per time step)
    by_t = df.groupby("t_granted", as_index=False).last().sort_values("t_granted")
    d_it = by_t["iter"].diff().fillna(0).astype(int)

    # Diagnostics
    n_total = by_t[["P_total", "Q_total"]].dropna().shape[0]
    n_vmag = by_t[["Vmag"]].dropna().shape[0]
    print(f"[INFO] Unique t_granted: {by_t['t_granted'].nunique()}")
    print(f"[INFO] Points with P/Q: {n_total}")
    print(f"[INFO] Points with Vmag: {n_vmag}")

    # Prepare x axis
    x, xlabel = _time_axis_seconds_or_hours(by_t["t_granted"])

    # 1) Total P/Q vs time
    d_pq = by_t.dropna(subset=["P_total", "Q_total"])
    plt.figure()
    if len(d_pq):
        x_pq, _ = _time_axis_seconds_or_hours(d_pq["t_granted"])
        plt.plot(x_pq, d_pq["P_total"], label="P_total (pu)")
        plt.plot(x_pq, d_pq["Q_total"], label="Q_total (pu)")
        plt.legend()
    plt.xlabel(xlabel)
    plt.ylabel("Total Distribution Load (pu)")
    plt.title("Total Distribution Load vs Time")
    plt.tight_layout()
    plt.savefig(out_dir / "total_pq_vs_time.png", dpi=300)
    plt.close()

    # 2) Bus |V| vs time
    d_v = by_t.dropna(subset=["Vmag"])
    plt.figure()
    if len(d_v):
        x_v, _ = _time_axis_seconds_or_hours(d_v["t_granted"])
        plt.plot(x_v, d_v["Vmag"])
    plt.xlabel(xlabel)
    plt.ylabel(f"Bus {bus} Voltage Magnitude |V| (pu)")
    plt.title(f"Bus {bus} Voltage Magnitude vs Time")
    plt.ylim(0.9, 1.0)
    plt.tight_layout()
    plt.savefig(out_dir / f"bus{bus}_voltage_vs_time.png", dpi=300)
    plt.close()

    # 3) Combined vertical subplots (shared x-axis)
    fig, ax = plt.subplots(2, 1, sharex=True, figsize=(8.5, 6.5))

    # Top: P/Q
    if len(d_pq):
        x_pq, _ = _time_axis_seconds_or_hours(d_pq["t_granted"])
        ax[0].plot(x_pq, d_pq["P_total"], label="P_total (pu)")
        ax[0].plot(x_pq, d_pq["Q_total"], label="Q_total (pu)")
        ax[0].set_ylabel("Total Load (pu)")
        ax[0].legend()
    ax[0].set_title("Total Load and Voltage vs Time")

    # Bottom: Vmag
    if len(d_v):
        x_v, _ = _time_axis_seconds_or_hours(d_v["t_granted"])
        ax[1].plot(x_v, d_v["Vmag"])
        ax[1].set_ylabel(f"Bus {bus} |V| (pu)")
    ax[1].set_xlabel(xlabel)


    plt.tight_layout()
    plt.savefig(out_dir / f"total_pq_and_bus{bus}_voltage_vs_time.png", dpi=300)
    plt.close(fig)

     # 4) Combined vertical subplots (shared x-axis)
    fig, ax = plt.subplots(2, 1, sharex=True, figsize=(8.5, 6.5))

    # Top: P/Q
    if len(d_pq):
        x_pq, _ = _time_axis_seconds_or_hours(d_pq["t_granted"])
        ax[0].plot(x_pq, d_pq["P_total"], label="P_total (pu)")
        ax[0].plot(x_pq, d_pq["Q_total"], label="Q_total (pu)")
        ax[0].set_ylabel("Total Load (pu)")
        ax[0].legend()
    ax[0].set_title("Total Load and Iteration vs Time")

    # Bottom: iteration versus time
    if len(d_v):
        x_v, _ = _time_axis_seconds_or_hours(d_v["t_granted"])
        ax[1].plot(x_v, d_v["iter"])
        ax[1].set_ylabel(f"Iteration at each time step")
    ax[1].set_xlabel(xlabel)

    plt.tight_layout()
    plt.savefig(out_dir / f"total_pq_and_iteration_vs_time.png", dpi=300)
    plt.close(fig)


    # Save CSV too
    csv_path = out_dir / "parsed_transmission.csv"
    df.to_csv(csv_path, index=False)
    print(f"[OK] Saved CSV: {csv_path}")
    print(f"[OK] Saved PNGs to: {out_dir}")
    print("     - total_pq_vs_time.png")
    print(f"     - bus{bus}_voltage_vs_time.png")
    print(f"     - total_pq_and_bus{bus}_voltage_vs_time.png")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", type=str, default="transmission.log", help="Path to transmission.log")
    ap.add_argument("--out", type=str, default=None, help="Output folder (default: same folder as log)")
    ap.add_argument("--bus", type=int, default=2, help="Bus index for |V| (default: 2)")
    args = ap.parse_args()

    log_path = Path(args.log).expanduser().resolve()
    if not log_path.exists():
        raise FileNotFoundError(f"Log not found: {log_path}")

    out_dir = Path(args.out).expanduser().resolve() if args.out else log_path.parent

    print(f"[INFO] Log: {log_path}")
    print(f"[INFO] Out: {out_dir}")
    print(f"[INFO] Bus: {args.bus}")

    df = parse_transmission_log(log_path, bus=args.bus)
    make_plots(df, out_dir, bus=args.bus)


if __name__ == "__main__":
    main()
