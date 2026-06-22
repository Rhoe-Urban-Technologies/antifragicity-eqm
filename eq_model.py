"""Iterative equilibrium model – the learning loop over multiple SUMO runs."""

import os
import math
import sys
import subprocess
import shutil
import xml.etree.ElementTree as ET

import numpy as np
import sumolib

from kpi_functions import (
    compute_throughput,
    compute_redundancy,
    compute_entropy,
    compute_stress,
    performance_index_limited,
    load_edgedata,
    _load_edge_speeds,
)

def get_initial_capacities(network_file):
    """
    Read the SUMO network and return a dictionary {edge_id: capacity}.
    Capacity is estimated as number_of_lanes × 1800 veh/h.
    """
    net = sumolib.net.readNet(network_file)
    initial_capacities = {}
    for edge in net.getEdges():
        edge_id = edge.getID()
        if edge_id.startswith(":"):  # skip internal edges
            continue
        lanes = edge.getLanes()
        if lanes:
            initial_capacities[edge_id] = len(lanes) * 1800.0
    return initial_capacities


def apply_capacities_to_network(base_network_file, output_network_file, target_capacities):
    """
    Write a modified SUMO network where lane speeds are scaled proportionally
    to the ratio (target_capacity / original_capacity). This acts as a proxy
    for adjusting throughput without changing the number of lanes.
    """
    original_capacities = get_initial_capacities(base_network_file)
    tree = ET.parse(base_network_file)
    root = tree.getroot()

    for edge_element in root.findall("edge"):
        edge_id = edge_element.get("id")
        if edge_id not in original_capacities:
            continue

        original_cap = original_capacities[edge_id]
        new_cap = target_capacities.get(edge_id, original_cap)
        scale_factor = new_cap / original_cap if original_cap > 0 else 1.0

        for lane_element in edge_element.findall("lane"):
            old_speed = float(lane_element.get("speed"))
            lane_element.set("speed", str(round(old_speed * scale_factor, 3)))

    tree.write(output_network_file, encoding="UTF-8", xml_declaration=True)


def run_sumo_simulation(network_file, tripinfo_output_rel, edgedata_output_rel,
                        scenario_dir, begin=0, end=90000):
    """
    Launch a SUMO simulation using the given network and the standard route files.
    The output file paths are relative to the scenario directory.
    """
    cfg_file = os.path.join(scenario_dir, "dua.static.sumocfg")
    modified_cfg_file = os.path.join(scenario_dir, "mod.iter.sumocfg")

    output_dir = os.path.dirname(os.path.join(scenario_dir, tripinfo_output_rel))
    os.makedirs(output_dir, exist_ok=True)

    tree = ET.parse(cfg_file)
    root = tree.getroot()
    for element_name, relative_path in [("tripinfo-output", tripinfo_output_rel),
                                        ("edgedata-output", edgedata_output_rel)]:
        element = root.find(f".//{element_name}")
        if element is not None:
            element.set("value", relative_path)
    tree.write(modified_cfg_file, encoding="UTF-8", xml_declaration=True)

    sumo_binary = "/Library/Frameworks/EclipseSUMO.framework/Versions/Current/EclipseSUMO/share/sumo/bin/sumo"
    absolute_network_file = os.path.abspath(network_file)

    additional_files = [
        os.path.join(scenario_dir, "vtypes.add.xml"),
        os.path.join(scenario_dir, "tll.static.xml"),
    ]

    route_files = [
        os.path.join(scenario_dir, "DUARoutes", "local.0.rou.xml"),
        os.path.join(scenario_dir, "DUARoutes", "local.1.rou.xml"),
        os.path.join(scenario_dir, "DUARoutes", "local.2.rou.xml"),
    ]

    command = [
        sumo_binary,
        "-c", modified_cfg_file,
        "--net-file", absolute_network_file,
        "--begin", str(begin),
        "--end", str(end),
        "--additional-files", ",".join(additional_files),
        "--route-files", ",".join(route_files),
    ]

    print("Running SUMO command:")
    print(" ".join(command))

    # Stream SUMO output line by line so progress is visible
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, bufsize=1)
    for line in process.stdout:
        print(line, end='')
    process.wait()
    if process.returncode != 0:
        raise RuntimeError(f"SUMO exited with code {process.returncode}")


def get_edge_stresses(edgedata_file, network_file, current_capacities=None):
    """
    Return a dictionary {edge_id: stress_value} for every non‑internal edge.
    The stress formula is the same as in compute_stress, applied per edge.
    If current_capacities is provided, it is used; otherwise the base lane count × 1800.
    """
    net = sumolib.net.readNet(network_file)
    flows = load_edgedata(edgedata_file)
    speeds = _load_edge_speeds(edgedata_file)
    edge_stress_dict = {}

    for edge in net.getEdges():
        edge_id = edge.getID()
        if edge_id.startswith(":"):
            continue

        flow = flows.get(edge_id, 0.0)
        lanes = edge.getLanes()
        if not lanes:
            continue

        if current_capacities is not None:
            capacity = current_capacities.get(edge_id, len(lanes) * 1800.0)
        else:
            capacity = len(lanes) * 1800.0

        free_flow_speed = float(lanes[0].getSpeed())
        if free_flow_speed <= 0:
            continue

        actual_speed = speeds.get(edge_id, free_flow_speed)
        actual_speed = max(actual_speed, 1e-3)

        volume_ratio = flow / capacity if capacity > 0 else 0.0
        speed_drop = max(0.0, 1.0 - actual_speed / free_flow_speed)
        stress = min(1.0, volume_ratio * speed_drop)
        edge_stress_dict[edge_id] = stress

    return edge_stress_dict


def eq_model(disaster_network_file, scenario_directory,
             max_iterations=30, learning_rate=0.1, memory_decay=math.exp(-1 / 30),
             capacity_min_factor=0.5, capacity_max_factor=2.0,
             convergence_tol=1e-4, verbose=True):
    """
    Run the iterative equilibrium learning loop.

    Returns
    -------
    final_E : float
        Performance index after convergence.
    final_capacities : dict
        Edge capacities (veh/h) at the end of the learning process.
    history : list of tuples
        (day, E, M, R, C, S) for each iteration.
    """
    # Initial capacities from the base (undisrupted) network
    base_capacities = get_initial_capacities(disaster_network_file)
    current_capacities = base_capacities.copy()

    # Memory variables for SRI
    infrastructure_pressure = 0.0
    previous_performance = None
    stable_count = 0
    history = []

    tripinfo_relative = "outputs/iter.tripinfo.xml"
    edgedata_relative = "outputs/iter.edgedata.xml"
    tripinfo_absolute = os.path.join(scenario_directory, tripinfo_relative)
    edgedata_absolute = os.path.join(scenario_directory, edgedata_relative)

    os.makedirs(os.path.dirname(tripinfo_absolute), exist_ok=True)

    for day in range(1, max_iterations + 1):
        # Write the network with the current capacities
        iterative_network_file = os.path.join(scenario_directory, "iter_lust.net.xml")
        apply_capacities_to_network(disaster_network_file, iterative_network_file,
                                    current_capacities)

        run_sumo_simulation(iterative_network_file, tripinfo_relative,
                            edgedata_relative, scenario_directory)

        throughput = compute_throughput(tripinfo_absolute)
        redundancy = compute_redundancy(iterative_network_file)
        entropy = compute_entropy(tripinfo_absolute)
        stress = compute_stress(iterative_network_file, edgedata_absolute)

        kpi_dict = {"M": throughput, "R": redundancy, "C": entropy, "S": stress}
        current_performance = performance_index_limited(kpi_dict)
        history.append((day, current_performance, throughput, redundancy, entropy, stress))

        if verbose:
            print(f"Day {day:2d}: E={current_performance:.4f}  "
                  f"M={throughput:.3f} R={redundancy:.3f} C={entropy:.3f} S={stress:.3f}")

        # ---------- Update SRI components ----------
        # Infrastructure pressure: exponential moving average of stress
        infrastructure_pressure = memory_decay * infrastructure_pressure + stress

        operational_elasticity = (
                                         redundancy * (1.0 - stress)
                                 ) / (1.0 + math.exp(-10.0 * (redundancy - 0.2)))

        # Currently equal weights; can become adaptive later
        sri = 0.5 * operational_elasticity + 0.5 * infrastructure_pressure

        # ---------- Per‑edge stress and short‑term gradient ----------
        edge_stresses = get_edge_stresses(edgedata_absolute, iterative_network_file,
                                          current_capacities)
        average_stress = stress  # the global S is the average over all edges

        short_term_gradient = {}
        for eid, edge_stress in edge_stresses.items():
            deviation = edge_stress - average_stress
            short_term_gradient[eid] = learning_rate * deviation * sri

        # ---------- Capacity update ----------
        new_capacities = {}
        for eid, cap in current_capacities.items():
            adjustment = short_term_gradient.get(eid, 0.0)
            updated_cap = cap * (1.0 + adjustment)

            # Clip to allowed range relative to the original base capacity
            base_cap = base_capacities.get(eid, cap)
            lower_bound = base_cap * capacity_min_factor
            upper_bound = base_cap * capacity_max_factor
            new_capacities[eid] = max(lower_bound, min(upper_bound, updated_cap))

        current_capacities = new_capacities

        # ---------- Convergence check ----------
        if previous_performance is not None and abs(current_performance - previous_performance) < convergence_tol:
            stable_count += 1
            if stable_count >= 5:
                if verbose:
                    print(f"\nConverged after {day} days.")
                break
        else:
            stable_count = 0
        previous_performance = current_performance

    # Final clean simulation with the converged capacities
    apply_capacities_to_network(disaster_network_file, iterative_network_file,
                                current_capacities)
    run_sumo_simulation(iterative_network_file, tripinfo_relative,
                        edgedata_relative, scenario_directory)

    final_throughput = compute_throughput(tripinfo_absolute)
    final_redundancy = compute_redundancy(iterative_network_file)
    final_entropy = compute_entropy(tripinfo_absolute)
    final_stress = compute_stress(iterative_network_file, edgedata_absolute)

    final_kpis = {"M": final_throughput, "R": final_redundancy,
                  "C": final_entropy, "S": final_stress}
    final_performance = performance_index_limited(final_kpis)

    if verbose:
        print(f"Final E = {final_performance:.4f}")

    return final_performance, current_capacities, history
