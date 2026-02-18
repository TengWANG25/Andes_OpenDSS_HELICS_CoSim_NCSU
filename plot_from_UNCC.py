import re
import pandas as pd
import matplotlib.pyplot as plt

def _to_complex(pair_text: str) -> complex:
    """Convert 're,im' into complex(re, im), tolerant to spaces."""
    re_im = [x.strip() for x in pair_text.split(",")]
    if len(re_im) != 2:
        raise ValueError(f"Cannot parse complex pair: {pair_text}")
    return complex(float(re_im[0]), float(re_im[1]))

def parse_gridpack_log(path: str, bus_id: int = 2) -> pd.DataFrame:
    """
    Parse a GridPACK-style co-sim log like:
      Time (s): 1
      S received..., Sa: (re,im) Sb: (re,im) Sc: (re,im)
      Bus 2: Va: (re,im), Vb: (re,im), Vc: (re,im)

    Returns a DataFrame with columns:
      t, Sa,Sb,Sc, Va,Vb,Vc (complex)
    """
    time_re = re.compile(r"Time\s*\(s\)\s*:\s*([0-9]*\.?[0-9]+)")
    s_re = re.compile(r"Sa:\s*\(([^)]+)\)\s*Sb:\s*\(([^)]+)\)\s*Sc:\s*\(([^)]+)\)")
    v_re = re.compile(
        rf"Bus\s+{bus_id}\s*:\s*Va:\s*\(([^)]+)\)\s*,\s*Vb:\s*\(([^)]+)\)\s*,\s*Vc:\s*\(([^)]+)\)"
    )

    rows = []
    current = {"t": None, "Sa": None, "Sb": None, "Sc": None, "Va": None, "Vb": None, "Vc": None}

    with open(path, "r", errors="ignore") as f:
        for line in f:
            line = line.strip()

            m = time_re.search(line)
            if m:
                if current["t"] is not None:
                    rows.append(current)
                current = {"t": float(m.group(1)), "Sa": None, "Sb": None, "Sc": None,
                           "Va": None, "Vb": None, "Vc": None}
                continue

            m = s_re.search(line)
            if m and current["t"] is not None:
                current["Sa"] = _to_complex(m.group(1))
                current["Sb"] = _to_complex(m.group(2))
                current["Sc"] = _to_complex(m.group(3))
                continue

            m = v_re.search(line)
            if m and current["t"] is not None:
                current["Va"] = _to_complex(m.group(1))
                current["Vb"] = _to_complex(m.group(2))
                current["Vc"] = _to_complex(m.group(3))
                continue

    if current["t"] is not None:
        rows.append(current)

    df = pd.DataFrame(rows)
    df = df.dropna(subset=["Sa", "Sb", "Sc", "Va", "Vb", "Vc"]).copy()
    df = df.sort_values("t").reset_index(drop=True)
    return df

def add_metrics(df: pd.DataFrame, baseMVA: float = 100.0) -> pd.DataFrame:
    """
    Adds:
      |Va|,|Vb|,|Vc|  (voltage magnitudes)
      Stot, Ptot, Qtot (total complex power and MW/Mvar on baseMVA)
    """
    out = df.copy()
    out["Vamag"] = out["Va"].abs()
    out["Vbmag"] = out["Vb"].abs()
    out["Vcmag"] = out["Vc"].abs()

    out["Stot"] = out["Sa"] + out["Sb"] + out["Sc"]
    out["Ptot_MW"] = out["Stot"].apply(lambda z: z.real) * baseMVA
    out["Qtot_Mvar"] = out["Stot"].apply(lambda z: z.imag) * baseMVA
    return out

def _complex_cols_to_csv_friendly(df: pd.DataFrame, cols) -> pd.DataFrame:
    """
    Convert complex columns into:
      <col>_re, <col>_im, and <col>_str = "(re,im)"
    so the CSV is easy to read and re-load.
    """
    out = df.copy()
    for c in cols:
        out[f"{c}_re"] = out[c].apply(lambda z: float(z.real))
        out[f"{c}_im"] = out[c].apply(lambda z: float(z.imag))
        out[f"{c}_str"] = out[c].apply(lambda z: f"({z.real},{z.imag})")
    return out

def save_results_to_csv(df: pd.DataFrame, out_csv: str):
    # Choose which complex columns to export
    complex_cols = ["Sa", "Sb", "Sc", "Va", "Vb", "Vc", "Stot"]
    df_csv = _complex_cols_to_csv_friendly(df, complex_cols)

    # (Optional) drop the raw complex columns to avoid "a+bj" formatting
    df_csv = df_csv.drop(columns=complex_cols)

    df_csv.to_csv(out_csv, index=False)
    print(f"[OK] Saved CSV: {out_csv}")

def plot_voltage_magnitudes(df: pd.DataFrame, title: str = "3-Phase Voltage Magnitudes vs Time"):
    plt.figure()
    plt.plot(df["t"], df["Vamag"], label="|Va|")
    plt.plot(df["t"], df["Vbmag"], label="|Vb|")
    plt.plot(df["t"], df["Vcmag"], label="|Vc|")
    plt.xlabel("Time (s)")
    plt.ylabel("Voltage magnitude (p.u.)")
    plt.ylim(0.9, 1.0)
    plt.title(title)
    plt.grid(True)
    plt.legend()
    plt.tight_layout()

def plot_total_power(df: pd.DataFrame, title: str = "Total 3-Phase Power vs Time"):
    plt.figure()
    plt.plot(df["t"], df["Ptot_MW"]/100, label="P_total (p.u.)")
    plt.plot(df["t"], df["Qtot_Mvar"]/100, label="Q_total (p.u.)")
    plt.xlabel("Time (s)")
    plt.ylabel("Power")
    plt.title(title)
    plt.grid(True)
    plt.legend()
    plt.tight_layout()

if __name__ == "__main__":
    # === CHANGE THIS PATH ===
    path = "gpk_118_10_bus_conn.csv"   # your log file (even if it's named .csv)
    bus_id = 2
    baseMVA = 100.0

    # === OUTPUT CSV ===
    out_csv = f"parsed_bus{bus_id}_metrics.csv"

    df_raw = parse_gridpack_log(path, bus_id=bus_id)
    df = add_metrics(df_raw, baseMVA=baseMVA)

    # Save to CSV
    save_results_to_csv(df, out_csv)

    # Quick check
    print(df[["t", "Vamag", "Vbmag", "Vcmag", "Ptot_MW", "Qtot_Mvar"]].head())

    # Plots
    plot_voltage_magnitudes(df, title=f"Bus {bus_id}: 3-Phase Voltage Magnitudes vs Time")
    plot_total_power(df, title=f"Bus {bus_id}: Total 3-Phase Power vs Time")

    plt.show()
