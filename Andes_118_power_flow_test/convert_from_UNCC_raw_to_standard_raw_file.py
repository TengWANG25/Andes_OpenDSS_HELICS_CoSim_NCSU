import sys
from pathlib import Path

def parse_template_buses(tpl_lines):
    """
    Parse bus metadata from a PSSE-style template RAW file.

    Returns a dict: bus_number -> dict(name, baskv, area, zone, owner)
    """
    buses = {}
    end_bus_idx = None
    for idx, line in enumerate(tpl_lines):
        if "END OF BUS DATA" in line:
            end_bus_idx = idx
            break
    if end_bus_idx is None:
        raise RuntimeError("Could not find 'END OF BUS DATA' in template file.")

    # Bus records are between line 4 (index 3) and end_bus_idx-1
    for line in tpl_lines[3:end_bus_idx]:
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 7:
            continue
        try:
            bus_no = int(parts[0])
        except ValueError:
            continue
        name = parts[1]             # keep quotes and spacing as-is
        baskv = float(parts[2])
        ide_tpl = int(parts[3])
        area = int(parts[4])
        zone = int(parts[5])
        owner = int(parts[6])
        buses[bus_no] = {
            "name": name,
            "baskv": baskv,
            "ide_tpl": ide_tpl,
            "area": area,
            "zone": zone,
            "owner": owner,
        }
    return buses

def parse_project_buses(proj_lines):
    """
    Parse the UNCC-style bus records from the project RAW file.
    - Header is first three lines.
    - Bus section continues until a line that is exactly '0'.
    """
    buses = {}
    # Skip first three lines
    idx = 3
    while idx < len(proj_lines):
        line = proj_lines[idx]
        if line.strip() == "0":
            break
        if not line.strip():
            idx += 1
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 12:
            raise RuntimeError(f"Unexpected bus line format at line {idx+1}: {line!r}")
        bus_no = int(parts[0])
        bus_type = int(parts[1])
        Pd = float(parts[2])
        Qd = float(parts[3])
        Gs = float(parts[4])
        Bs = float(parts[5])
        area = int(parts[6])
        Vm = float(parts[7])
        Va = float(parts[8])   # convert degrees to radians
        name = parts[9]          # includes quotes
        vn_col = float(parts[10])
        last_int = int(parts[11])
        buses[bus_no] = {
            "type": bus_type,
            "Pd": Pd,
            "Qd": Qd,
            "Gs": Gs,
            "Bs": Bs,
            "area": area,
            "Vm": Vm,
            "Va": Va,
            "name_proj": name,
            "vn_col": vn_col,
            "last_int": last_int,
        }
        idx += 1

    bus_end_idx = idx  # index of line with just '0'
    return buses, bus_end_idx

def build_bus_section(template_buses, project_buses):
    """
    Construct PSSE bus records using topology/meta from template and
    Vm/Va/type from project.
    """
    lines = []
    for bus_no in sorted(template_buses.keys()):
        meta = template_buses[bus_no]
        pdata = project_buses.get(bus_no)
        if pdata is None:
            raise RuntimeError(f"Bus {bus_no} present in template but missing in project file.")
        name = meta["name"]
        baskv = meta["baskv"]
        area = meta["area"]
        zone = meta["zone"]
        owner = meta["owner"]
        ide = pdata["type"]  # use project bus type
        vm = pdata["Vm"]
        va = pdata["Va"]
        # Format similar to PSSE, but spacing is not critical
        line = f"{bus_no:6d},{name:>13s},{baskv:8.4f},{ide:1d},{area:4d},{zone:4d},{owner:4d},{vm:8.5f},{va:9.4f}"
        lines.append(line)
    return lines

def build_load_section(template_buses, project_buses, pl_tol=1e-6):
    """
    Build LOAD DATA section (type 1 loads) from project Pd/Qd.
    Representation: pure constant-power loads.
    """
    lines = []
    for bus_no in sorted(project_buses.keys()):
        pdata = project_buses[bus_no]
        Pd = pdata["Pd"]
        Qd = pdata["Qd"]
        if abs(Pd) < pl_tol and abs(Qd) < pl_tol:
            continue  # no load at this bus
        meta = template_buses.get(bus_no)
        if meta is None:
            raise RuntimeError(f"Bus {bus_no} present in project but missing in template file.")
        area = meta["area"]
        zone = meta["zone"]
        owner = meta["owner"]
        I = bus_no
        ID = "'1 '"
        STATUS = 1
        IP = IQ = YP = YQ = 0.0
        SCALE = 1
        line = (
            f"{I:6d},{ID},"
            f"{STATUS:1d},{area:4d},{zone:4d},"
            f"{Pd:11.3f},{Qd:11.3f},"
            f"{IP:11.3f},{IQ:11.3f},{YP:11.3f},{YQ:11.3f},"
            f"{owner:4d},{SCALE:d}"
        )
        lines.append(line)
    return lines

def build_shunt_section(project_buses, gb_tol=1e-6):
    """
    Build FIXED SHUNT DATA section from project Gs/Bs (shunt admittances).
    """
    lines = []
    for bus_no in sorted(project_buses.keys()):
        pdata = project_buses[bus_no]
        Gs = pdata["Gs"]
        Bs = pdata["Bs"]
        if abs(Gs) < gb_tol and abs(Bs) < gb_tol:
            continue
        I = bus_no
        ID = "'1 '"
        STATUS = 1
        line = f"{I:6d},{ID},{STATUS:1d},{Gs:11.3f},{Bs:11.3f}"
        lines.append(line)
    return lines

def find_generator_block(proj_lines, bus_end_idx):
    """
    Find generator data block in the project file after bus section.

    Returns (gen_start_idx, gen_end_idx, tail_start_idx)
    where:
      - gen_start_idx: index of first generator record
      - gen_end_idx: index of last generator record
      - tail_start_idx: index of line with 'END OF GENERATOR DATA...'
    """
    # First non-empty line after the '0' line is the first generator record
    idx = bus_end_idx + 1
    while idx < len(proj_lines) and not proj_lines[idx].strip():
        idx += 1
    gen_start = idx

    gen_end_marker_idx = None
    for j in range(gen_start, len(proj_lines)):
        if "END OF GENERATOR DATA" in proj_lines[j]:
            gen_end_marker_idx = j
            break
    if gen_end_marker_idx is None:
        raise RuntimeError("Could not find 'END OF GENERATOR DATA' in project file.")
    gen_end = gen_end_marker_idx - 1
    tail_start = gen_end_marker_idx
    return gen_start, gen_end, tail_start

def convert(uncc_path, template_path, output_path):
    uncc_text = Path(uncc_path).read_text(encoding="latin-1").splitlines()
    tpl_text = Path(template_path).read_text(encoding="latin-1").splitlines()

    # Header: reuse template header 1, use UNCC title line, add a conversion note
    header1 = tpl_text[0]
    header2 = uncc_text[1] if len(uncc_text) > 1 else tpl_text[1]
    header3 = "CONVERTED FROM UNCC FORMAT BY SCRIPT"
    header_lines = [header1, header2, header3]

    template_buses = parse_template_buses(tpl_text)
    project_buses, bus_end_idx = parse_project_buses(uncc_text)

    # Build new BUS, LOAD, and FIXED SHUNT sections
    bus_section = build_bus_section(template_buses, project_buses)
    load_section = build_load_section(template_buses, project_buses)
    shunt_section = build_shunt_section(project_buses)

    # Generator and the rest: copy from UNCC file
    gen_start, gen_end, tail_start = find_generator_block(uncc_text, bus_end_idx)
    gen_lines = uncc_text[gen_start:gen_end+1]
    tail_lines = uncc_text[tail_start:]

    out_lines = []
    # Header
    out_lines.extend(header_lines)
    # BUS DATA
    out_lines.extend(bus_section)
    out_lines.append("0 / END OF BUS DATA, BEGIN LOAD DATA")
    # LOAD DATA
    out_lines.extend(load_section)
    out_lines.append("0 / END OF LOAD DATA, BEGIN FIXED SHUNT DATA")
    # FIXED SHUNT DATA
    out_lines.extend(shunt_section)
    out_lines.append("0 / END OF FIXED SHUNT DATA, BEGIN GENERATOR DATA")
    # GENERATOR DATA
    out_lines.extend(gen_lines)
    # Remaining sections (END OF GENERATOR DATA, BRANCH DATA, etc.)
    out_lines.extend(tail_lines)

    Path(output_path).write_text("\n".join(out_lines) + "\n", encoding="latin-1")
    print(f"Wrote converted file to {output_path}")

def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if len(argv) != 3:
        print("Usage: python convert_uncc_to_psse.py <uncc_raw> <template_psse_raw> <output_raw>")
        sys.exit(1)
    uncc_path, template_path, output_path = argv
    convert(uncc_path, template_path, output_path)

if __name__ == "__main__":
    main()
