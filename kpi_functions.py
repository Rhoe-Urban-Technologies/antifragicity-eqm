"""
KPI Calculator for the Three-Layer Equilibrium Model (Tier 2 core indicators).

Reads SUMO simulation outputs (tripinfo, edgedata) and the network file,
returns normalised values in [0,1] for:
    M - Mobility throughput (fraction of demand satisfied)
    R - Redundancy (average normalised edge-disjoint paths)
    C - Network entropy (normalised Shannon entropy of OD flows)
    S - Stress index (average volume/capacity × speed drop)
"""
import math
import os
import random
import xml.etree.ElementTree as ET
from collections import defaultdict
from typing import Optional

import networkx as nx
import sumolib


# ---------------------------------------------------------------------------
# 1. Mobility Throughput (M)
# ---------------------------------------------------------------------------

def _infer_summary_file(tripinfo_file: str) -> str | None:
    """Infer the sibling summary file for a given tripinfo path."""
    directory, filename = os.path.split(tripinfo_file)
    if "tripinfo" not in filename:
        return None
    summary_file = os.path.join(directory, filename.replace("tripinfo", "summary"))
    return summary_file if os.path.exists(summary_file) else None


def load_summary_counts(summary_file: str) -> dict:
    """
    Parse the final <step> record from a SUMO summary XML file.

    Returns integer-valued counters such as loaded, inserted, arrived,
    teleports, discarded, and running when present.
    """
    tree = ET.parse(summary_file)
    last_step = None
    for step in tree.getroot().findall("step"):
        last_step = step

    if last_step is None:
        return {}

    counts = {}
    for key, value in last_step.attrib.items():
        try:
            counts[key] = int(float(value))
        except (TypeError, ValueError):
            counts[key] = value
    return counts


def compute_throughput(tripinfo_file: str, summary_file: Optional[str] = None) -> float:
    """
    Fraction of loaded demand that successfully arrived within the simulation
    horizon.

    Formula
    -------
    Preferred definition (when SUMO summary output is available):

        M = N_arrived / N_loaded

    where
        N_arrived = number of <tripinfo> elements with an 'arrival' attribute,
        N_loaded  = final 'loaded' counter from summary-output.

    This fixes a known bias in tripinfo-only accounting: vehicles discarded
    before insertion due to `max-depart-delay` never appear in tripinfo, but
    they are included in the summary 'loaded' demand and should reduce M.

    If no summary file is provided (or inferable), the function falls back to
    the legacy approximation:

        M = N_arrived / N_tripinfo

    Notes
    -----
    If SUMO teleports stuck vehicles to a downstream edge and lets them finish,
    they may still count as arrived. For a strict throughput interpretation,
    configure the simulation with `time-to-teleport.remove="true"` and
    `collision.action="remove"`.

    Returns
    -------
    float
        M in [0,1].  1.0 = all trips completed.
    """
    tree = ET.parse(tripinfo_file)
    trips = tree.getroot().findall("tripinfo")
    total = 0
    arrived = 0
    for trip in trips:
        total += 1
        if trip.get("arrival") is not None:  # trip completed
            arrived += 1

    resolved_summary = summary_file or _infer_summary_file(tripinfo_file)
    if resolved_summary and os.path.exists(resolved_summary):
        counts = load_summary_counts(resolved_summary)
        loaded = counts.get("loaded", 0)
        return arrived / loaded if loaded > 0 else 0.0

    return arrived / total if total > 0 else 0.0


# ---------------------------------------------------------------------------
# 2. Network Redundancy (R)
# ---------------------------------------------------------------------------

def build_graph(net_file: str) -> nx.DiGraph:
    """Build a directed graph from a SUMO .net.xml file."""
    net = sumolib.net.readNet(net_file)
    G = nx.DiGraph()
    for node in net.getNodes():
        G.add_node(node.getID())
    for edge in net.getEdges():
        frm = edge.getFromNode().getID()
        to = edge.getToNode().getID()
        G.add_edge(frm, to, edge_id=edge.getID())
    return G


def _sample_od_pairs(G, num_pairs=500):
    """Random OD pairs, avoiding loops."""
    nodes = list(G.nodes())
    if len(nodes) < 2:
        return []
    pairs = [(random.choice(nodes), random.choice(nodes)) for _ in range(num_pairs)]
    return [(o, d) for o, d in pairs if o != d]


def compute_redundancy(net_file: str,
                       od_pairs: Optional[list] = None,
                       sample_size: int = 500) -> float:
    """
    Average number of edge‑disjoint paths per OD pair, normalised by the
    average node out‑degree (as an upper bound).

    Formula
    -------
    For each OD pair (o,d):
        k_{o,d} = number of edge‑disjoint paths from o to d

    R = min( 1,  avg(k_{o,d}) / avg_out_degree )

    where avg_out_degree = (1/|V|) * sum_{v in V} out_degree(v).

    Parameters
    ----------
    net_file : str
        Path to SUMO .net.xml.
    od_pairs : list of (orig, dest) tuples, optional
        If None, a random sample is drawn.
    sample_size : int
        Number of random OD pairs to sample (if od_pairs not provided).

    Returns
    -------
    float
        R in [0,1].  0 = no alternative paths, 1 = many alternatives.
    """
    G = build_graph(net_file)
    if od_pairs is None:
        od_pairs = _sample_od_pairs(G, sample_size)

    # Normalisation factor: average out-degree
    avg_deg = sum(d for _, d in G.out_degree()) / G.number_of_nodes()
    if avg_deg == 0:
        return 0.0

    disjoint_counts = []
    for o, d in od_pairs:
        if o == d:
            continue
        try:
            # edge-disjoint paths; networkx returns a generator of paths
            count = sum(1 for _ in nx.edge_disjoint_paths(G, o, d))
            disjoint_counts.append(count)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            disjoint_counts.append(0)

    if not disjoint_counts:
        return 0.0

    avg_disjoint = sum(disjoint_counts) / len(disjoint_counts)
    # Normalise: max possible disjoint paths is roughly avg_deg (degree bound)
    return min(1.0, avg_disjoint / avg_deg)


# ---------------------------------------------------------------------------
# 3. Network Entropy of Mobility Flows (C)
# ---------------------------------------------------------------------------

def _load_od_flows(tripinfo_file: str) -> dict:
    """Build OD flow dictionary from tripinfo (origin, destination) -> count."""
    tree = ET.parse(tripinfo_file)
    flows = defaultdict(int)
    for trip in tree.getroot().findall("tripinfo"):
        depart_lane = trip.get("departLane", "")
        arrive_lane = trip.get("arrivalLane", "")
        origin = depart_lane.rsplit("_", 1)[0] if depart_lane else None
        destination = arrive_lane.rsplit("_", 1)[0] if arrive_lane else None
        if origin and destination:
            flows[(origin, destination)] += 1
    return dict(flows)


def _route_edges_from_vehicle(vehicle: ET.Element, route_edge_map: dict) -> tuple[Optional[str], Optional[str]]:
    """Extract origin/destination edges for a <vehicle> element."""
    origin = vehicle.get("from")
    destination = vehicle.get("to")
    if origin and destination:
        return origin, destination

    route_id = vehicle.get("route")
    if route_id and route_id in route_edge_map:
        return route_edge_map[route_id]

    for child in vehicle:
        if child.tag != "route":
            continue
        child_edges = child.get("edges")
        if child_edges:
            edges = child_edges.split()
            if edges:
                return edges[0], edges[-1]
        child_route_id = child.get("refId")
        if child_route_id and child_route_id in route_edge_map:
            return route_edge_map[child_route_id]

    return None, None


def _load_od_flows_from_route_files(route_files: list[str]) -> dict:
    """
    Build OD demand dictionary from SUMO route files.

    This captures all planned demand (loaded by SUMO), including vehicles that
    may later be discarded before departure and never appear in tripinfo.
    """
    flows = defaultdict(int)
    route_edge_map = {}
    pending_route_refs = defaultdict(int)

    for route_file in route_files:
        if not os.path.exists(route_file):
            continue

        for _, element in ET.iterparse(route_file, events=("end",)):
            tag = element.tag

            if tag == "route":
                route_id = element.get("id")
                edges_attr = element.get("edges")
                if route_id and edges_attr:
                    edges = edges_attr.split()
                    if edges:
                        route_edge_map[route_id] = (edges[0], edges[-1])
                        pending = pending_route_refs.pop(route_id, 0)
                        if pending > 0:
                            flows[(edges[0], edges[-1])] += pending
                    element.clear()
                continue

            amount = 1
            amount_attr = element.get("number") if tag == "flow" else None
            if amount_attr:
                try:
                    amount = int(float(amount_attr))
                except ValueError:
                    amount = 1

            origin = None
            destination = None

            if tag == "trip":
                origin = element.get("from")
                destination = element.get("to")

            elif tag in ("vehicle", "flow"):
                origin = element.get("from")
                destination = element.get("to")

                if not (origin and destination):
                    route_ref = element.get("route")
                    if route_ref and route_ref in route_edge_map:
                        origin, destination = route_edge_map[route_ref]
                    elif route_ref:
                        pending_route_refs[route_ref] += amount

                if not (origin and destination):
                    for child in element:
                        if child.tag != "route":
                            continue
                        child_edges = child.get("edges")
                        if child_edges:
                            edges = child_edges.split()
                            if edges:
                                origin, destination = edges[0], edges[-1]
                                break
                        child_ref = child.get("refId")
                        if child_ref and child_ref in route_edge_map:
                            origin, destination = route_edge_map[child_ref]
                            break
                        if child_ref:
                            pending_route_refs[child_ref] += amount
                            break

            if origin and destination:
                flows[(origin, destination)] += amount

            element.clear()

    return dict(flows)


def compute_entropy(tripinfo_file: str, route_files: Optional[list[str]] = None) -> float:
    """
    Normalised Shannon entropy of the OD flow distribution.

    Formula
    -------
    Let p_i = f_i / Σ f_j   (fraction of total flow on OD pair i)

    H = - Σ p_i * log2(p_i)            (Shannon entropy)
    H_max = log2(K)                    (maximum entropy for K OD pairs)

    C = H / H_max

    Input modes
    -----------
    route_files is None:
        OD flows are computed from tripinfo (executed trips only).
    route_files provided:
        OD flows are computed from planned demand in route files, which keeps
        trips discarded before departure in the entropy accounting.

    Returns
    -------
    float
        C in [0,1].  0 = completely concentrated flows, 1 = perfectly uniform.
    """
    od_flows = (
        _load_od_flows_from_route_files(route_files)
        if route_files
        else _load_od_flows(tripinfo_file)
    )
    total = sum(od_flows.values())
    if total == 0 or len(od_flows) <= 1:
        return 0.0
    entropy = -sum((c / total) * math.log2(c / total) for c in od_flows.values() if c > 0)
    max_entropy = math.log2(len(od_flows))
    return entropy / max_entropy


# ---------------------------------------------------------------------------
# 4. Stress Index (S)
# ---------------------------------------------------------------------------

def load_edgedata(edgedata_file: str) -> dict:
    """
    Parse edgedata XML, return dict mapping edge id -> total vehicles entered.
    (Speed data requires lane-level extraction; see _load_edge_speeds.)
    """
    tree = ET.parse(edgedata_file)
    flows = {}
    for interval in tree.getroot().findall("interval"):
        for edge in interval.findall("edge"):
            edge_id = edge.get("id")
            if edge_id and not edge_id.startswith(":"):
                count = float(edge.get("entered") or edge.get("left") or 0)
                flows[edge_id] = flows.get(edge_id, 0) + count
    return flows


def _load_edge_speeds(edgedata_file: str) -> dict:
    """
    Extract average speed per edge from edgedata (if available).
    Falls back to traveltime attribute if speed not present.
    """
    tree = ET.parse(edgedata_file)
    speeds = {}
    for interval in tree.getroot().findall("interval"):
        for edge in interval.findall("edge"):
            edge_id = edge.get("id")
            if edge_id and not edge_id.startswith(":"):
                # Some edgedata outputs include speed attribute
                sp = edge.get("speed")
                if sp is not None:
                    speeds[edge_id] = float(sp)
    return speeds


def compute_stress(net_file: str,
                   edgedata_file: str,
                   capacity_per_lane: float = 1800.0,
                   sim_duration_hours: float = 25.0) -> float:
    """
    Average over all edges of:
        min( (flow / capacity) * (1 - actual_speed / free_flow_speed), 1.0 )

    Formula (per edge a)
    --------------------
    stress_a = min( (f_a / C_a) * (1 - v_a / v0_a), 1.0 )

    where
        f_a   = flow on edge a (veh over the period),
        C_a   = capacity of edge a (lanes * capacity_per_lane),
        v_a   = actual speed on edge a (m/s),
        v0_a  = free‑flow speed of edge a (m/s).

    S = (1 / |A|) * Σ_a stress_a        (average over all non‑internal edges)

    Parameters
    ----------
    net_file : str
        Path to .net.xml.
    edgedata_file : str
        Path to edgedata.xml (must contain flow and speed data).
    capacity_per_lane : float
        Approximate capacity per lane (veh/h).

    Returns
    -------
    float
        S in [0,1].  0 = no stress, 1 = extreme congestion.
    """
    net = sumolib.net.readNet(net_file)
    flow_dict = load_edgedata(edgedata_file)
    speed_dict = _load_edge_speeds(edgedata_file)

    edge_stresses = []
    for edge in net.getEdges():
        eid = edge.getID()
        if eid.startswith(":"):  # internal edges
            continue

        # Flow (vehicles over the simulation period; assuming hourly rate)
        flow = flow_dict.get(eid, 0.0)
        flow_rate = flow / sim_duration_hours

        # Capacity: number of lanes × capacity_per_lane
        lanes = edge.getLanes()
        if not lanes:
            continue
        capacity = len(lanes) * capacity_per_lane

        # Free-flow speed (m/s) from the network
        free_speed = float(lanes[0].getSpeed()) if lanes else 0.0
        if free_speed <= 0:
            continue

        # Actual speed (m/s) from edgedata
        actual_speed = speed_dict.get(eid, free_speed)  # default to free-flow if missing
        # Avoid zero division / negative speed
        actual_speed = max(actual_speed, 1e-3)

        volume_ratio = flow_rate / capacity if capacity > 0 else 0.0
        speed_drop = max(0.0, 1.0 - actual_speed / free_speed)
        edge_stress = min(1.0, volume_ratio * speed_drop)
        edge_stresses.append(edge_stress)

    if not edge_stresses:
        return 0.0
    return sum(edge_stresses) / len(edge_stresses)


# ---------------------------------------------------------------------------
# 5. Performance Index – Full Tier‑2 (6 core KPIs)
# ---------------------------------------------------------------------------

def performance_index(kpis: dict) -> float:
    """
    Full Tier‑2 composite performance index as defined in the vulnerability
    assessment deliverable (Equation 6).

    Requires all six core KPIs in the [0,1] range:
        M – Mobility Throughput
        R – Redundancy
        C – Network Entropy
        S – Stress Index
        Q – Equity (Theil‑T)
        I – Intermodal Synergy

    Formula
    -------
    E_Tier2 = (M + R + C + I - S - Q + 2) / 6

    Returns
    -------
    float
        Performance index in [0,1]. Higher values indicate better system
        performance under the given conditions.
    """
    return (kpis["M"] + kpis["R"] + kpis["C"] + kpis["I"]
            - kpis["S"] - kpis["Q"] + 2) / 6


# ---------------------------------------------------------------------------
# 6. Performance Index – Limited (4 core KPIs, Q and I absent)
# ---------------------------------------------------------------------------

def performance_index_limited(kpis: dict) -> float:
    """
    Temporary 4‑KPI adaptation of the performance index for use when equity (Q)
    and intermodal synergy (I) are not yet available.

    Uses only the four measurable core KPIs:
        M – Mobility Throughput
        R – Redundancy
        C – Network Entropy
        S – Stress Index

    Formula
    -------
    E_limited = (M + R + C - S + 1) / 4

    This formula preserves the [0,1] range under the same assumptions (all KPIs
    in [0,1]) and maintains the structural principle of rewarding high M, R, C
    while penalising S.  It is **not** the official Tier‑2 index and must be
    replaced with the full 6‑KPI version when Q and I become available.

    Returns
    -------
    float
        Performance index in [0,1].  Higher values indicate better performance.
    """
    return (kpis["M"] + kpis["R"] + kpis["C"] - kpis["S"] + 1) / 4


# ---------------------------------------------------------------------------
# 7. System Vulnerability (ΔE)
# ---------------------------------------------------------------------------

def system_vulnerability(E_base: float, E_post: float) -> float:
    """
    Relative performance loss due to a disruption, measured using the same
    performance index for both states.

    Definition
    ----------
    ΔE = 1 - E_post / E_base       (if E_base > 0)

    Interpretation
    --------------
    • ΔE = 0    → no loss (post‑event performance ≥ baseline)
    • ΔE > 0    → positive loss (higher means more vulnerable)
    • ΔE < 0    → system improved (post‑event performance > baseline);
                    typically reported as zero vulnerability.

    Parameters
    ----------
    E_base : float
        Performance index in the baseline (undisturbed) state.
    E_post : float
        Performance index in the post‑disaster state.

    Returns
    -------
    float
        Vulnerability score.  Negative values are possible and indicate an
        improvement; for reporting these are usually clamped to zero.
    """
    return 1.0 - E_post / E_base if E_base != 0 else 0.0
