"""Iterative equilibrium model – the learning loop over multiple SUMO runs."""

import math
import os
import xml.etree.ElementTree as ET

from kpi_functions import (
    DEFAULT_CAPACITY_PER_LANE,
    _read_net,
    compute_edge_stresses,
    compute_link_entropy,
    compute_redundancy,
    compute_throughput,
    performance_index_limited,
)
from sumo_runner import run_sumo, write_hourly_edgedata_additional, write_run_config

# Additional files in the static configs that write hourly edgedata for the
# static baseline/disaster runs. They are removed from the learning-loop
# config so iterations don't overwrite those outputs; the loop writes its own
# hourly edgedata instead.
STATIC_HOURLY_ADDITIONALS = (
    "edgedata.hourly.baseline.add.xml",
    "edgedata.hourly.disaster.add.xml",
)


def get_initial_capacities(network_file, capacity_per_lane=DEFAULT_CAPACITY_PER_LANE):
    """
    Read the SUMO network and return a dictionary {edge_id: capacity}.
    Capacity is estimated as number_of_lanes × capacity_per_lane (veh/h).
    """
    initial_capacities = {}
    for edge in _read_net(network_file).getEdges():
        edge_id = edge.getID()
        if edge_id.startswith(":"):  # skip internal edges
            continue
        lanes = edge.getLanes()
        if lanes:
            initial_capacities[edge_id] = len(lanes) * capacity_per_lane
    return initial_capacities


def apply_capacities_to_network(base_network_file, output_network_file, target_capacities,
                                max_speed_factor=1.3, capacity_per_lane=DEFAULT_CAPACITY_PER_LANE):
    """
    Write a modified SUMO network where lane speeds are scaled proportionally
    to the ratio (target_capacity / original_capacity). This acts as a proxy
    for adjusting throughput without changing the number of lanes.

    The speed scale factor is capped at `max_speed_factor`, so speed limits
    stay plausible (e.g. 1.3 × 130 km/h ≈ 170 km/h) even when the learned
    capacity is up to `capacity_max_factor` × base. Capacity reductions are
    applied in full.
    """
    original_capacities = get_initial_capacities(base_network_file, capacity_per_lane)
    tree = ET.parse(base_network_file)
    root = tree.getroot()

    for edge_element in root.findall("edge"):
        edge_id = edge_element.get("id")
        if edge_id not in original_capacities:
            continue

        original_cap = original_capacities[edge_id]
        new_cap = target_capacities.get(edge_id, original_cap)
        scale_factor = new_cap / original_cap if original_cap > 0 else 1.0
        scale_factor = min(scale_factor, max_speed_factor)

        for lane_element in edge_element.findall("lane"):
            old_speed_attr = lane_element.get("speed")
            if old_speed_attr is None:
                continue
            old_speed = float(old_speed_attr)
            lane_element.set("speed", str(round(old_speed * scale_factor, 3)))

    tree.write(output_network_file, encoding="UTF-8", xml_declaration=True)


def run_sumo_simulation(network_file, tripinfo_output_rel, edgedata_output_rel,
                        scenario_dir, begin=0, end=90000, summary_output_rel=None,
                        base_cfg="disaster.sumocfg", log_output_rel="outputs/iter.log",
                        additional_files=None, route_files=None,
                        hourly_edgedata_rel=None, hourly_period=3600,
                        cfg_name="mod.iter.sumocfg", progress_desc="SUMO"):
    """
    Launch a SUMO simulation of `base_cfg` with `network_file` swapped in.

    Route files and additional files (including the disaster rerouter
    `disaster_closures.add.xml`) are taken from `base_cfg`, so the run
    reproduces the same disruption, demand (incl. buses/transit) and bus stops
    as the static post-disaster simulation. Pass `additional_files` /
    `route_files` (lists of paths) only to deliberately override the config.

    If `hourly_edgedata_rel` is given, an additional edgedata output
    aggregated every `hourly_period` seconds is written there (needed for
    windowed KPIs).

    Output file paths are relative to the scenario directory.
    """
    if summary_output_rel is None:
        summary_output_rel = tripinfo_output_rel.replace("tripinfo", "summary")

    tripinfo_output_abs = os.path.join(scenario_dir, tripinfo_output_rel)
    os.makedirs(os.path.dirname(tripinfo_output_abs), exist_ok=True)

    extra_additional = []
    if hourly_edgedata_rel is not None:
        hourly_add_rel = os.path.splitext(hourly_edgedata_rel)[0] + ".add.xml"
        write_hourly_edgedata_additional(os.path.join(scenario_dir, hourly_add_rel),
                                         os.path.join(scenario_dir, hourly_edgedata_rel),
                                         period=hourly_period)
        extra_additional.append(hourly_add_rel)

    modified_cfg_file = os.path.join(scenario_dir, cfg_name)
    write_run_config(
        os.path.join(scenario_dir, base_cfg), modified_cfg_file,
        outputs={
            "summary-output": summary_output_rel,
            "tripinfo-output": tripinfo_output_rel,
            "edgedata-output": edgedata_output_rel,
            "log": log_output_rel,
        },
        extra_additional_files=extra_additional if additional_files is None else (),
        remove_additional_files=STATIC_HOURLY_ADDITIONALS,
    )

    extra_args = []
    if additional_files is not None:
        all_additional = list(additional_files) + [os.path.join(scenario_dir, a)
                                                   for a in extra_additional]
        extra_args += ["--additional-files", ",".join(all_additional)]
    if route_files is not None:
        extra_args += ["--route-files", ",".join(route_files)]

    run_sumo(modified_cfg_file, net_file=network_file, begin=begin, end=end,
             extra_args=extra_args, progress_desc=progress_desc)


def _capacity_factor_stats(capacities, base_capacities):
    factors = [capacities[eid] / base for eid, base in base_capacities.items() if base > 0]
    if not factors:
        return {"capacity_factor_mean": 1.0, "capacity_factor_min": 1.0,
                "capacity_factor_max": 1.0}
    return {
        "capacity_factor_mean": sum(factors) / len(factors),
        "capacity_factor_min": min(factors),
        "capacity_factor_max": max(factors),
    }


def eq_model(disaster_network_file, scenario_directory,
             max_iterations=30, learning_rate=0.1, memory_decay=math.exp(-1 / 30),
             capacity_min_factor=0.5, capacity_max_factor=2.0, max_speed_factor=1.3,
             convergence_tol=1e-4, verbose=True, begin_time=0, end_time=90000,
             base_cfg="disaster.sumocfg", stress_window=None, entropy_window=None,
             capacity_per_lane=DEFAULT_CAPACITY_PER_LANE):
    """
    Run the iterative equilibrium learning loop.

    Parameters
    ----------
    disaster_network_file : str
        The network whose capacities are adapted; it should be the same
        network used by the static post-disaster run (the base `lust.net.xml`,
        with closures applied via the rerouter in `base_cfg`). It also supplies
        the lane counts and *original* free-flow speeds for the stress index,
        so raising a capacity (which raises the simulated speed limit) does not
        inflate the measured speed drop.
    scenario_directory : str
        Directory containing `base_cfg`; outputs go to its `outputs/` folder.
    capacity_min_factor, capacity_max_factor : float
        Bounds on each edge's learned capacity relative to its base capacity.
    max_speed_factor : float
        Cap on the speed-limit scaling used as the capacity proxy in SUMO.
    begin_time, end_time : int
        Simulated horizon (s), passed to SUMO. The default (0–90000 s, 25 h)
        matches `disaster.sumocfg` and the baseline run.
    stress_window, entropy_window : (begin_s, end_s), optional
        Time windows for S and C (e.g. the closure hour (61200, 64800)).
        None = whole horizon. Windowed KPIs use an hourly edgedata output the
        loop writes itself.
    capacity_per_lane : float
        Nominal capacity per lane (veh/h).

    Returns
    -------
    final_E : float
        Performance index of a final simulation with the learned capacities.
    final_capacities : dict
        Edge capacities (veh/h) at the end of the learning process.
    history : list of dict
        One entry per day: day, E, M, R, C, S, SRI components and capacity
        factor statistics (capacities used for that day relative to base).
        The last entry (day == "final") describes the final simulation.
    """
    base_capacities = get_initial_capacities(disaster_network_file, capacity_per_lane)
    current_capacities = base_capacities.copy()

    # Memory variables for SRI
    infrastructure_pressure = 0.0
    previous_performance = None
    stable_count = 0
    history = []

    tripinfo_relative = "outputs/iter.tripinfo.xml"
    edgedata_relative = "outputs/iter.edgedata.xml"
    summary_relative = "outputs/iter.summary.xml"
    hourly_edgedata_relative = "outputs/iter.hourly.edgedata.xml"
    tripinfo_absolute = os.path.join(scenario_directory, tripinfo_relative)
    edgedata_absolute = os.path.join(scenario_directory, edgedata_relative)
    summary_absolute = os.path.join(scenario_directory, summary_relative)
    hourly_edgedata_absolute = os.path.join(scenario_directory, hourly_edgedata_relative)
    iterative_network_file = os.path.join(scenario_directory, "iter_lust.net.xml")

    os.makedirs(os.path.dirname(tripinfo_absolute), exist_ok=True)

    # Redundancy only depends on the network topology, which capacity updates
    # (speed scaling) don't change, so it is computed once.
    redundancy = compute_redundancy(disaster_network_file)

    def simulate_and_evaluate(capacities, label):
        apply_capacities_to_network(disaster_network_file, iterative_network_file,
                                    capacities, max_speed_factor=max_speed_factor,
                                    capacity_per_lane=capacity_per_lane)
        run_sumo_simulation(iterative_network_file, tripinfo_relative,
                            edgedata_relative, scenario_directory,
                            begin=begin_time, end=end_time,
                            summary_output_rel=summary_relative,
                            base_cfg=base_cfg,
                            hourly_edgedata_rel=hourly_edgedata_relative,
                            progress_desc=label)

        throughput = compute_throughput(tripinfo_absolute, summary_absolute)
        entropy = compute_link_entropy(
            hourly_edgedata_absolute if entropy_window else edgedata_absolute,
            disaster_network_file, window=entropy_window)
        edge_stresses = compute_edge_stresses(
            disaster_network_file,
            hourly_edgedata_absolute if stress_window else edgedata_absolute,
            capacity_per_lane=capacity_per_lane, window=stress_window,
            capacities=capacities)
        stress = (sum(edge_stresses.values()) / len(edge_stresses)) if edge_stresses else 0.0
        kpis = {"M": throughput, "R": redundancy, "C": entropy, "S": stress}
        return kpis, edge_stresses

    for day in range(1, max_iterations + 1):
        kpi_dict, edge_stresses = simulate_and_evaluate(current_capacities, f"Day {day}/{max_iterations}")
        current_performance = performance_index_limited(kpi_dict)

        if verbose:
            print(f"Day {day:2d}: E={current_performance:.4f}  "
                  f"M={kpi_dict['M']:.3f} R={kpi_dict['R']:.3f} "
                  f"C={kpi_dict['C']:.3f} S={kpi_dict['S']:.3f}")

        # ---------- Update SRI components ----------
        stress = kpi_dict["S"]
        # Infrastructure pressure: exponential moving average of stress
        infrastructure_pressure = memory_decay * infrastructure_pressure + stress
        operational_elasticity = (
            (redundancy * (1.0 - stress)) / (1.0 + math.exp(-10.0 * (redundancy - 0.2)))
        )
        # Currently equal weights; can become adaptive later
        sri = 0.5 * operational_elasticity + 0.5 * infrastructure_pressure

        history.append({
            "day": day, "E": current_performance, **kpi_dict,
            "sri": sri, "operational_elasticity": operational_elasticity,
            "infrastructure_pressure": infrastructure_pressure,
            **_capacity_factor_stats(current_capacities, base_capacities),
        })

        # ---------- Short-term gradient ----------
        # S is the mean of these per-edge stresses (same capacities, same
        # window), so the deviations are centred on zero.
        short_term_gradient = {
            eid: learning_rate * (edge_stress - stress) * sri
            for eid, edge_stress in edge_stresses.items()
        }

        # ---------- Capacity update ----------
        new_capacities = {}
        for eid, cap in current_capacities.items():
            updated_cap = cap * (1.0 + short_term_gradient.get(eid, 0.0))
            # Clip to allowed range relative to the original base capacity
            base_cap = base_capacities.get(eid, cap)
            new_capacities[eid] = max(base_cap * capacity_min_factor,
                                      min(base_cap * capacity_max_factor, updated_cap))
        current_capacities = new_capacities

        # ---------- Convergence check ----------
        if (previous_performance is not None
                and abs(current_performance - previous_performance) < convergence_tol):
            stable_count += 1
            if stable_count >= 5:
                if verbose:
                    print(f"\nConverged after {day} days.")
                break
        else:
            stable_count = 0
        previous_performance = current_performance

    # Final clean simulation with the learned capacities
    final_kpis, _ = simulate_and_evaluate(current_capacities, "Final run")
    final_performance = performance_index_limited(final_kpis)
    history.append({"day": "final", "E": final_performance, **final_kpis,
                    **_capacity_factor_stats(current_capacities, base_capacities)})

    if verbose:
        print(f"Final E = {final_performance:.4f}")

    return final_performance, current_capacities, history
