"""
KPI Calculator for the Three-Layer Equilibrium Model (Tier 2 core indicators).

Reads SUMO simulation outputs (tripinfo, edgedata) and the network file,
returns normalised values in [0,1] for:
    M - Mobility throughput (fraction of demand satisfied)
    R - Redundancy (average normalised edge-disjoint paths)
    C - Network entropy (normalised Shannon entropy of link flows, D3.2 Eq. 17;
        OD-based variants are also available)
    S - Stress index (average volume/capacity × speed drop)
"""
import math
import os
import random
import xml.etree.ElementTree as ET
from collections import defaultdict
from functools import lru_cache
from typing import Optional

import networkx as nx
import sumolib

DEFAULT_CAPACITY_PER_LANE = 1800.0  # veh/h


@lru_cache(maxsize=2)
def _read_net_cached(abs_path: str, mtime: float):
    return sumolib.net.readNet(abs_path)


def _read_net(net_file: str):
    """
    Read a SUMO network, reusing the parsed object while the file is
    unchanged (LuST takes several seconds to parse and several KPIs need it).
    Callers must not modify the returned network.
    """
    abs_path = os.path.abspath(net_file)
    return _read_net_cached(abs_path, os.path.getmtime(abs_path))


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
    net = _read_net(net_file)
    G = nx.DiGraph()
    for node in net.getNodes():
        G.add_node(node.getID())
    for edge in net.getEdges():
        frm = edge.getFromNode().getID()
        to = edge.getToNode().getID()
        G.add_edge(frm, to, edge_id=edge.getID())
    return G


DEFAULT_REDUNDANCY_SEED = 42


def _sample_od_pairs(G, num_pairs=500, seed: Optional[int] = DEFAULT_REDUNDANCY_SEED):
    """
    Random OD pairs, avoiding loops.

    Nodes are sorted before sampling and a dedicated RNG seeded with `seed` is
    used, so the same network (or two networks with the same node set, e.g.
    baseline vs. post-disaster) always yields the same OD sample. Pass
    seed=None for non-deterministic sampling.
    """
    nodes = sorted(G.nodes())
    if len(nodes) < 2:
        return []
    rng = random.Random(seed)
    pairs = [(rng.choice(nodes), rng.choice(nodes)) for _ in range(num_pairs)]
    return [(o, d) for o, d in pairs if o != d]


def compute_redundancy(net_file: str,
                       od_pairs: Optional[list] = None,
                       sample_size: int = 500,
                       seed: Optional[int] = DEFAULT_REDUNDANCY_SEED) -> float:
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
    seed : int or None
        Seed for OD-pair sampling. A fixed seed keeps R reproducible and makes
        baseline and post-disaster runs use the same OD sample, so differences
        in R reflect the network rather than sampling noise. None = random.

    Returns
    -------
    float
        R in [0,1].  0 = no alternative paths, 1 = many alternatives.
    """
    G = build_graph(net_file)
    if od_pairs is None:
        od_pairs = _sample_od_pairs(G, sample_size, seed)

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

    Note: D3.2 Annex B (Eq. 17) defines C over *link* flows; that is
    implemented in `compute_link_entropy`, which is the default C used by the
    examples and the learning loop. This OD-based variant is kept for
    comparison. With `route_files` it measures planned demand only, so it is
    identical for every scenario that loads the same routes.

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


def compute_link_entropy(edgedata_file: str, net_file: str,
                         *, window: Optional[tuple] = None) -> float:
    """
    Normalised Shannon entropy of the flow distribution over network links
    (D3.2 Annex B, Indicator R2, Eq. 17). This is the default C.

    Formula
    -------
    p_i = f_i / Σ_j f_j        (share of the total flow on link i)

    C = - Σ_i p_i log(p_i) / log(N)

    where f_i is the number of vehicles that entered link i (edgedata
    `entered`) and N is the total number of (non-internal) links in the
    network, including links without flow.

    Unlike the OD-based `compute_entropy`, this reacts to disruptions: when
    links are closed, flow concentrates on fewer links and C drops.

    Parameters
    ----------
    edgedata_file : str
        SUMO edgedata output.
    net_file : str
        Network defining the set of links (N).
    window : (begin_s, end_s), optional
        Only use edgedata intervals inside this window (requires an edgedata
        output with a matching period, e.g. hourly). None = all intervals.

    Returns
    -------
    float
        C in [0,1]. 0 = all flow on one link, 1 = flow spread evenly over all
        links.
    """
    flows, _, _ = aggregate_edgedata(edgedata_file, window=window)
    link_ids = [edge.getID() for edge in _read_net(net_file).getEdges()
                if not edge.getID().startswith(":")]
    n_links = len(link_ids)
    if n_links <= 1:
        return 0.0
    link_flows = [flows.get(eid, 0.0) for eid in link_ids]
    total = sum(link_flows)
    if total <= 0:
        return 0.0
    entropy = -sum((f / total) * math.log(f / total) for f in link_flows if f > 0)
    return entropy / math.log(n_links)


# ---------------------------------------------------------------------------
# 4. Stress Index (S)
# ---------------------------------------------------------------------------

def load_edgedata_intervals(edgedata_file: str) -> list[dict]:
    """
    Parse a SUMO edgedata file into its aggregation intervals.

    Returns
    -------
    list of dict
        One entry per <interval>: {"begin": float, "end": float,
        "edges": {edge_id: {"entered": float, "speed": float | None,
        "sampledSeconds": float | None}}}. Internal edges (":...") are skipped.
    """
    intervals = []
    for _, element in ET.iterparse(edgedata_file, events=("end",)):
        if element.tag != "interval":
            continue
        edges = {}
        for edge in element.findall("edge"):
            edge_id = edge.get("id")
            if not edge_id or edge_id.startswith(":"):
                continue
            speed = edge.get("speed")
            sampled = edge.get("sampledSeconds")
            edges[edge_id] = {
                "entered": float(edge.get("entered") or edge.get("left") or 0),
                "speed": float(speed) if speed is not None else None,
                "sampledSeconds": float(sampled) if sampled is not None else None,
            }
        intervals.append({"begin": float(element.get("begin")),
                          "end": float(element.get("end")),
                          "edges": edges})
        element.clear()
    return intervals


def aggregate_edgedata(edgedata_file: str,
                       window: Optional[tuple] = None) -> tuple[dict, dict, float]:
    """
    Aggregate edgedata over all intervals, or over the intervals inside
    `window`.

    Flows are summed. Speeds are averaged weighted by `sampledSeconds`
    (vehicle-seconds on the edge), which is how SUMO itself aggregates speed;
    edges without `sampledSeconds` fall back to an unweighted mean.

    Parameters
    ----------
    edgedata_file : str
        SUMO edgedata output.
    window : (begin_s, end_s), optional
        Only intervals fully inside the window are used, and together they
        must cover it exactly. Use an edgedata output whose period divides the
        window (e.g. hourly edgedata for a 17:00–18:00 window).

    Returns
    -------
    (flows, speeds, duration_hours)
        flows : {edge_id: vehicles entered during the selected intervals}
        speeds : {edge_id: mean speed in m/s} (only edges with speed data)
        duration_hours : total length of the selected intervals in hours
    """
    intervals = load_edgedata_intervals(edgedata_file)
    if window is not None:
        w_begin, w_end = window
        selected = [iv for iv in intervals
                    if iv["begin"] >= w_begin - 1e-6 and iv["end"] <= w_end + 1e-6]
        covered = sum(iv["end"] - iv["begin"] for iv in selected)
        if not selected or abs(covered - (w_end - w_begin)) > 1e-6:
            available = [(iv["begin"], iv["end"]) for iv in intervals[:5]]
            raise ValueError(
                f"Edgedata intervals in {edgedata_file} do not cover the window "
                f"{window} (first intervals: {available}). Use an edgedata "
                f"output aggregated hourly (period=3600)."
            )
    else:
        selected = intervals

    flows = defaultdict(float)
    speed_weighted_sum = defaultdict(float)
    speed_weight = defaultdict(float)
    for interval in selected:
        for edge_id, data in interval["edges"].items():
            flows[edge_id] += data["entered"]
            if data["speed"] is None:
                continue
            weight = data["sampledSeconds"] if data["sampledSeconds"] is not None else 1.0
            if weight <= 0:
                continue
            speed_weighted_sum[edge_id] += data["speed"] * weight
            speed_weight[edge_id] += weight

    speeds = {eid: speed_weighted_sum[eid] / speed_weight[eid] for eid in speed_weight}
    duration_hours = sum(iv["end"] - iv["begin"] for iv in selected) / 3600.0
    return dict(flows), speeds, duration_hours


def load_edgedata(edgedata_file: str) -> dict:
    """Edge id -> total vehicles entered, summed over all intervals."""
    return aggregate_edgedata(edgedata_file)[0]


def _load_edge_speeds(edgedata_file: str) -> dict:
    """Edge id -> mean speed (m/s) over all intervals, weighted by sampledSeconds."""
    return aggregate_edgedata(edgedata_file)[1]


def compute_edge_stresses(net_file: str,
                          edgedata_file: str,
                          *,
                          capacity_per_lane: float = DEFAULT_CAPACITY_PER_LANE,
                          sim_duration_hours: Optional[float] = None,
                          window: Optional[tuple] = None,
                          capacities: Optional[dict] = None) -> dict:
    """
    Per-edge stress for every non-internal edge of `net_file`.

    Formula (per edge a)
    --------------------
    stress_a = min( (f_a / C_a) * (1 - v_a / v0_a), 1.0 )

    where
        f_a   = flow on edge a (veh/h, vehicles entered ÷ period length),
        C_a   = capacity of edge a (veh/h): `capacities[a]` if given, else
                lanes × capacity_per_lane,
        v_a   = mean speed on edge a over the period (m/s),
        v0_a  = free-flow speed of edge a (m/s), from `net_file`.

    Parameters
    ----------
    net_file : str
        Network supplying lane counts and free-flow speeds. When the simulated
        network has modified speeds (the learning loop), pass the *original*
        network here so that v0 is the real free-flow speed and raising
        capacity does not raise measured stress.
    edgedata_file : str
        SUMO edgedata output with `entered` and `speed`.
    capacity_per_lane : float
        Nominal capacity per lane (veh/h), used when `capacities` has no entry.
    sim_duration_hours : float, optional
        Overrides the period length used to turn counts into hourly flows.
        By default it is taken from the edgedata intervals, so it always
        matches the simulated horizon. Not allowed together with `window`.
    window : (begin_s, end_s), optional
        Measure stress only over this time window (e.g. the closure hour).
        Requires edgedata aggregated with a period that divides the window.
    capacities : dict, optional
        {edge_id: capacity in veh/h}, e.g. the learned capacities.

    Returns
    -------
    dict
        {edge_id: stress in [0,1]}
    """
    if window is not None and sim_duration_hours is not None:
        raise ValueError("Pass either window or sim_duration_hours, not both.")

    net = _read_net(net_file)
    flows, speeds, duration_hours = aggregate_edgedata(edgedata_file, window=window)
    hours = sim_duration_hours if sim_duration_hours is not None else duration_hours
    if hours <= 0:
        raise ValueError(f"Non-positive period length ({hours} h) for {edgedata_file}")

    edge_stresses = {}
    for edge in net.getEdges():
        eid = edge.getID()
        if eid.startswith(":"):  # internal edges
            continue
        lanes = edge.getLanes()
        if not lanes:
            continue

        nominal_capacity = len(lanes) * capacity_per_lane
        capacity = capacities.get(eid, nominal_capacity) if capacities else nominal_capacity

        free_speed = float(lanes[0].getSpeed())
        if free_speed <= 0:
            continue

        flow_rate = flows.get(eid, 0.0) / hours
        # Default to free-flow speed when the edge has no speed data
        actual_speed = max(speeds.get(eid, free_speed), 1e-3)

        volume_ratio = flow_rate / capacity if capacity > 0 else 0.0
        speed_drop = max(0.0, 1.0 - actual_speed / free_speed)
        edge_stresses[eid] = min(1.0, volume_ratio * speed_drop)
    return edge_stresses


def compute_stress(net_file: str,
                   edgedata_file: str,
                   *,
                   capacity_per_lane: float = DEFAULT_CAPACITY_PER_LANE,
                   sim_duration_hours: Optional[float] = None,
                   window: Optional[tuple] = None,
                   capacities: Optional[dict] = None) -> float:
    """
    Stress index S: the unweighted average of `compute_edge_stresses` over all
    non-internal edges.

    S = (1 / |A|) * Σ_a stress_a

    All options after `edgedata_file` are keyword-only (passing the period
    length positionally used to land in `capacity_per_lane`). See
    `compute_edge_stresses` for the per-edge formula and the parameters.

    Returns
    -------
    float
        S in [0,1].  0 = no stress, 1 = extreme congestion.
    """
    edge_stresses = compute_edge_stresses(
        net_file, edgedata_file,
        capacity_per_lane=capacity_per_lane,
        sim_duration_hours=sim_duration_hours,
        window=window,
        capacities=capacities,
    )
    if not edge_stresses:
        return 0.0
    return sum(edge_stresses.values()) / len(edge_stresses)


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


# ---------------------------------------------------------------------------
# 8. Impact classification (D3.2, Table 9 / Eq. 20)
# ---------------------------------------------------------------------------

# (upper bound, class number, label). Class bounds follow D3.2 Table 9;
# values exactly on a bound belong to the lower class (Eq. 10 uses
# right-closed intervals, e.g. (0.15, 0.30] = Moderate), except that
# Table 9 states Negligible as "< 0.05", so 0.05 itself is Minor.
IMPACT_CLASSES = [
    (0.15, 2, "Minor"),
    (0.30, 3, "Moderate"),
    (0.50, 4, "Major"),
    (math.inf, 5, "Catastrophic"),
]


def classify_vulnerability(delta: float) -> tuple[int, str]:
    """
    Map a vulnerability score (ΔE_s, or a group-level ΔQ_s) to the D3.2
    impact scale (Table 9, also Eq. 20 interpretation):

        ΔE ≤ 0            → 0  No performance loss
        0    < ΔE < 0.05  → 1  Negligible
        0.05 ≤ ΔE ≤ 0.15  → 2  Minor
        0.15 < ΔE ≤ 0.30  → 3  Moderate
        0.30 < ΔE ≤ 0.50  → 4  Major
        ΔE > 0.50         → 5  Catastrophic

    The D3.2 equity escalation rule (r_equity > 1.5 raises the class by one,
    Eq. 27) is not applied here because r_equity is not computed.

    Returns
    -------
    (class_number, label)
    """
    if delta <= 0:
        return 0, "No performance loss"
    if delta < 0.05:
        return 1, "Negligible"
    for upper, cls, label in IMPACT_CLASSES:
        if delta <= upper:
            return cls, label
    return 5, "Catastrophic"  # unreachable (inf bound); keeps type checkers happy
