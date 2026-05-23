#!/usr/bin/env python3
"""
Generate a simplified one-line topology graph from an OpenDSS feeder folder.

Usage examples:
  python generate_dss_oneline.py /path/to/Run_8500Node.dss --output ieee8500_oneline --mode reduced
  python generate_dss_oneline.py /path/to/master.dss --output ieee8500_oneline --mode both --source sourcebus

What it does:
  1) Recursively follows Compile/Redirect/OpenDSS include commands.
  2) Extracts bus-to-bus connections from Line.* and Transformer.* objects.
  3) Uses bus coordinate files when available; otherwise creates a radial BFS layout.
  4) Optionally reduces the graph by collapsing degree-2 chain nodes, which makes the
     IEEE 8500-node feeder much more readable as a one-line overview.
"""

from __future__ import annotations

import argparse
import math
import os
import re
import sys
from collections import defaultdict, deque
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import matplotlib.pyplot as plt
import networkx as nx

COMMENT_RE = re.compile(r"(!.*$|//.*$)")
INCLUDE_RE = re.compile(r"^\s*(compile|redirect)\s+(.+?)\s*$", re.IGNORECASE)
NEW_EDIT_RE = re.compile(r"^\s*(new|edit)\s+([^\s]+)(.*)$", re.IGNORECASE)
KV_RE = re.compile(r"(\w+)\s*=\s*(\[[^\]]*\]|\([^\)]*\)|\"[^\"]*\"|'[^']*'|\S+)", re.IGNORECASE)

TOPO_CLASSES = {"line", "transformer"}


def strip_comments(line: str) -> str:
    # Good enough for typical DSS files. Avoids parsing comments as commands.
    return COMMENT_RE.sub("", line).strip()


def unwrap_path(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("(") and raw.endswith(")"):
        raw = raw[1:-1]
    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
        raw = raw[1:-1]
    return raw.strip()


def resolve_include(raw: str, base_dir: Path) -> Optional[Path]:
    target = unwrap_path(raw)
    # Commands may be like: redirect file.dss, or compile (master.dss)
    # Remove trailing DSS command tokens if they accidentally appear.
    target = target.split()[0] if target and not target.startswith(('"', "'")) else target
    p = Path(target)
    if not p.is_absolute():
        p = base_dir / p
    return p if p.exists() else None


def read_dss_recursive(entry: Path) -> Tuple[List[Tuple[Path, str]], List[str]]:
    """Return flattened DSS commands and warnings."""
    visited: set[Path] = set()
    commands: List[Tuple[Path, str]] = []
    warnings: List[str] = []

    def _read(path: Path) -> None:
        path = path.resolve()
        if path in visited:
            return
        visited.add(path)
        try:
            text = path.read_text(errors="ignore")
        except Exception as exc:
            warnings.append(f"Could not read {path}: {exc}")
            return

        pending = ""
        for raw in text.splitlines():
            line = strip_comments(raw)
            if not line:
                continue
            if line.startswith("~"):
                pending += " " + line[1:].strip()
                continue
            if pending:
                _handle_command(path, pending)
            pending = line
        if pending:
            _handle_command(path, pending)

    def _handle_command(path: Path, cmd: str) -> None:
        commands.append((path, cmd))
        m = INCLUDE_RE.match(cmd)
        if m:
            inc = resolve_include(m.group(2), path.parent)
            if inc:
                _read(inc)
            else:
                warnings.append(f"Missing include referenced from {path.name}: {m.group(2).strip()}")

    _read(entry)
    return commands, warnings


def parse_kv(rest: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for k, v in KV_RE.findall(rest):
        out[k.lower()] = v.strip().rstrip(",")
    return out


def all_kv_values(rest: str, key: str) -> List[str]:
    pat = re.compile(rf"\b{re.escape(key)}\s*=\s*(\[[^\]]*\]|\([^\)]*\)|\"[^\"]*\"|'[^']*'|\S+)", re.IGNORECASE)
    return [v.strip().rstrip(",") for v in pat.findall(rest)]


def split_list_value(value: str) -> List[str]:
    value = unwrap_path(value)
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    if value.startswith("(") and value.endswith(")"):
        value = value[1:-1]
    value = value.replace(",", " ")
    return [tok for tok in value.split() if tok]


def normalize_bus(bus: str) -> str:
    bus = unwrap_path(bus).strip().strip("[],")
    if not bus:
        return bus
    # OpenDSS bus phases are appended after the first dot, e.g., 650.1.2.3 -> 650.
    # Bus names in the IEEE feeders normally do not require dots in the base name.
    return bus.split(".")[0].lower()


def parse_bus_coords(commands: Sequence[Tuple[Path, str]]) -> Dict[str, Tuple[float, float]]:
    coords: Dict[str, Tuple[float, float]] = {}
    skip_prefixes = (
        "new", "edit", "set", "redirect", "compile", "solve", "show", "export", "plot",
        "calc", "clear", "interpolate", "summary", "buscoords"
    )
    for _path, cmd in commands:
        s = cmd.strip()
        if not s or s.lower().startswith(skip_prefixes):
            continue
        parts = s.replace(",", " ").split()
        if len(parts) >= 3:
            try:
                x = float(parts[1])
                y = float(parts[2])
            except ValueError:
                continue
            coords[normalize_bus(parts[0])] = (x, y)
    return coords


def parse_elements(commands: Sequence[Tuple[Path, str]]) -> Tuple[nx.MultiGraph, Dict[str, str], List[str]]:
    G = nx.MultiGraph()
    element_type_by_edge: Dict[str, str] = {}
    source_candidates: List[str] = []

    for path, cmd in commands:
        m = NEW_EDIT_RE.match(cmd)
        if not m:
            continue
        obj = m.group(2)
        rest = m.group(3)
        if "." not in obj:
            cls, name = obj.lower(), obj
        else:
            cls, name = obj.split(".", 1)
            cls = cls.lower()
        kv = parse_kv(rest)

        if cls in {"circuit", "vsource"}:
            b = kv.get("bus1") or kv.get("bus")
            if b:
                source_candidates.append(normalize_bus(b))

        if cls == "line":
            b1 = kv.get("bus1")
            b2 = kv.get("bus2")
            if b1 and b2:
                u, v = normalize_bus(b1), normalize_bus(b2)
                if u and v and u != v:
                    eid = f"Line.{name}"
                    G.add_edge(u, v, key=eid, element=eid, kind="line")
                    element_type_by_edge[eid] = "line"

        elif cls == "transformer":
            buses: List[str] = []
            if "buses" in kv:
                buses = split_list_value(kv["buses"])
            else:
                buses = all_kv_values(rest, "bus")
            buses = [normalize_bus(b) for b in buses if normalize_bus(b)]
            # For 2-winding transformers, connect bus 1 to bus 2. For 3-winding, connect primary to each other winding.
            if len(buses) >= 2:
                primary = buses[0]
                for b in buses[1:]:
                    if primary != b:
                        eid = f"Transformer.{name}"
                        G.add_edge(primary, b, key=f"{eid}:{b}", element=eid, kind="transformer")
                        element_type_by_edge[eid] = "transformer"

    # Store source hints on graph metadata.
    G.graph["source_candidates"] = source_candidates
    return G, element_type_by_edge, source_candidates


def choose_source(G: nx.Graph, requested: Optional[str] = None) -> Optional[str]:
    if requested:
        r = normalize_bus(requested)
        if r in G:
            return r
    for cand in G.graph.get("source_candidates", []):
        if cand in G:
            return cand
    if not G.nodes:
        return None
    # Distribution feeders are often radial; the source is commonly near a high-degree/root node.
    return max(G.degree, key=lambda x: x[1])[0]


def reduce_degree_two_chains(G: nx.MultiGraph, source: Optional[str]) -> nx.Graph:
    H_simple = nx.Graph(G)
    if H_simple.number_of_nodes() == 0:
        return nx.Graph()

    keep = {n for n in H_simple.nodes if H_simple.degree(n) != 2}
    if source:
        keep.add(source)
    # If a component is a pure cycle, keep one node from it.
    for comp in nx.connected_components(H_simple):
        if not (set(comp) & keep):
            keep.add(next(iter(comp)))

    R = nx.Graph()
    for n in keep:
        R.add_node(n, original_nodes=1)

    visited_directed = set()
    for start in list(keep):
        for nbr in H_simple.neighbors(start):
            if (start, nbr) in visited_directed:
                continue
            path = [start, nbr]
            prev, cur = start, nbr
            visited_directed.add((prev, cur))
            while cur not in keep:
                nexts = [x for x in H_simple.neighbors(cur) if x != prev]
                if not nexts:
                    break
                nxt = nexts[0]
                prev, cur = cur, nxt
                path.append(cur)
                visited_directed.add((prev, cur))
            end = path[-1]
            if start != end:
                R.add_edge(start, end, segments=len(path) - 1, collapsed_nodes=max(0, len(path) - 2))
    return R


def bfs_layout(G: nx.Graph, source: Optional[str]) -> Dict[str, Tuple[float, float]]:
    if G.number_of_nodes() == 0:
        return {}
    roots: List[str] = []
    if source and source in G:
        roots.append(source)
    roots += [next(iter(c)) for c in nx.connected_components(G) if not (source and source in c)]

    pos: Dict[str, Tuple[float, float]] = {}
    y_offset = 0.0
    for root in roots:
        comp_nodes = nx.node_connected_component(G, root)
        dist = nx.single_source_shortest_path_length(G.subgraph(comp_nodes), root)
        levels: Dict[int, List[str]] = defaultdict(list)
        for node, depth in dist.items():
            levels[depth].append(node)
        max_width = max((len(v) for v in levels.values()), default=1)
        for depth in sorted(levels):
            nodes = sorted(levels[depth])
            for i, node in enumerate(nodes):
                # Center each level vertically.
                y = y_offset + i - (len(nodes) - 1) / 2
                pos[node] = (float(depth), float(y))
        y_offset += max_width + 5.0
    return pos


def scale_coords(coords: Dict[str, Tuple[float, float]]) -> Dict[str, Tuple[float, float]]:
    if not coords:
        return coords
    xs = [p[0] for p in coords.values()]
    ys = [p[1] for p in coords.values()]
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    dx = max(maxx - minx, 1.0)
    dy = max(maxy - miny, 1.0)
    return {n: ((x - minx) / dx, (y - miny) / dy) for n, (x, y) in coords.items()}


def get_positions(G: nx.Graph, coords: Dict[str, Tuple[float, float]], source: Optional[str]) -> Dict[str, Tuple[float, float]]:
    usable = {n: coords[n] for n in G.nodes if n in coords}
    if len(usable) >= 0.8 * max(1, G.number_of_nodes()):
        return scale_coords(usable)
    return bfs_layout(G, source)


def draw_graph(G: nx.Graph, pos: Dict[str, Tuple[float, float]], output: Path, title: str, source: Optional[str], label_limit: int = 250) -> None:
    if G.number_of_nodes() == 0:
        raise ValueError("No topology edges were found. Upload/run this script from the folder containing master.dss and all redirected DSS files.")

    n = G.number_of_nodes()
    e = G.number_of_edges()
    width = max(12, min(60, 0.018 * n + 12))
    height = max(8, min(50, 0.014 * n + 8))

    fig, ax = plt.subplots(figsize=(width, height))
    ax.set_title(f"{title}\n{n:,} buses, {e:,} branches", fontsize=12)
    ax.axis("off")

    node_size = 3 if n > 2000 else 8 if n > 500 else 25
    line_width = 0.25 if e > 2000 else 0.5 if e > 500 else 1.0

    nx.draw_networkx_edges(G, pos, ax=ax, width=line_width, alpha=0.75)
    nx.draw_networkx_nodes(G, pos, ax=ax, node_size=node_size, linewidths=0)

    if source and source in G:
        nx.draw_networkx_nodes(G, pos, nodelist=[source], ax=ax, node_size=max(50, node_size * 8), linewidths=1.0)
        ax.text(pos[source][0], pos[source][1], f"  source: {source}", fontsize=9, va="center")

    if n <= label_limit:
        nx.draw_networkx_labels(G, pos, ax=ax, font_size=6)
    else:
        # Label only high-degree nodes and the source for readability.
        important = sorted(G.degree, key=lambda x: x[1], reverse=True)[:40]
        labels = {node: node for node, deg in important if deg >= 3}
        if source and source in G:
            labels[source] = source
        nx.draw_networkx_labels(G, pos, labels=labels, ax=ax, font_size=6)

    fig.tight_layout()
    fig.savefig(output.with_suffix(".png"), dpi=300)
    fig.savefig(output.with_suffix(".svg"))
    plt.close(fig)


def write_summary(path: Path, G: nx.MultiGraph, R: Optional[nx.Graph], warnings: List[str], source: Optional[str], coords: Dict[str, Tuple[float, float]]) -> None:
    comps = nx.number_connected_components(nx.Graph(G)) if G.number_of_nodes() else 0
    lines = [
        "OpenDSS one-line graph summary",
        "================================",
        f"Source/root bus used: {source or 'not found'}",
        f"Full graph: {G.number_of_nodes():,} buses, {G.number_of_edges():,} branches, {comps:,} connected component(s)",
        f"Bus coordinates found: {len(coords):,}",
    ]
    if R is not None:
        lines.append(f"Reduced graph: {R.number_of_nodes():,} retained buses, {R.number_of_edges():,} retained branches")
    if warnings:
        lines += ["", "Warnings:"] + [f"- {w}" for w in warnings]
    path.write_text("\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate an OpenDSS feeder one-line graph as PNG/SVG.")
    parser.add_argument("dss_file", type=Path, help="Run_8500Node.dss, master.dss, or another DSS entry file")
    parser.add_argument("--output", type=Path, default=Path("dss_oneline"), help="Output basename without extension")
    parser.add_argument("--mode", choices=["full", "reduced", "both"], default="reduced", help="Draw full graph, reduced graph, or both")
    parser.add_argument("--source", default=None, help="Optional source/root bus name, e.g., sourcebus")
    args = parser.parse_args()

    if not args.dss_file.exists():
        print(f"ERROR: {args.dss_file} does not exist", file=sys.stderr)
        return 2

    commands, warnings = read_dss_recursive(args.dss_file)
    G, _edge_types, _source_candidates = parse_elements(commands)
    coords = parse_bus_coords(commands)
    source = choose_source(G, args.source)

    if G.number_of_edges() == 0:
        write_summary(args.output.with_name(args.output.name + "_summary.txt"), G, None, warnings, source, coords)
        print("No topology edges were found.")
        print("This usually means the entry file only calls Compile/Redirect, but the referenced master.dss or feeder files are missing.")
        print(f"Summary written to {args.output.with_name(args.output.name + '_summary.txt')}")
        return 1

    R = None
    if args.mode in {"reduced", "both"}:
        R = reduce_degree_two_chains(G, source)
        pos_R = get_positions(R, coords, source)
        draw_graph(R, pos_R, args.output.with_name(args.output.name + "_reduced"), "Reduced one-line topology", source)

    if args.mode in {"full", "both"}:
        pos_G = get_positions(nx.Graph(G), coords, source)
        draw_graph(nx.Graph(G), pos_G, args.output.with_name(args.output.name + "_full"), "Full one-line topology", source)

    write_summary(args.output.with_name(args.output.name + "_summary.txt"), G, R, warnings, source, coords)
    print(f"Done. Wrote outputs next to: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
