"""Luxembourg vulnerability assessment + iterative equilibrium model."""
import json
import sys, os
sys.path.append(os.path.dirname(__file__))

from kpi_functions import (
    compute_throughput, compute_redundancy, compute_entropy, compute_stress,
    performance_index_limited, system_vulnerability
)
from eq_model import eq_model
from datetime import datetime

LUST_BASE = "AntifragiCity-WP4/T4.1-KPIs/LuSTScenario-master"

BASELINE_NET = os.path.join(LUST_BASE, "scenario", "lust.net.xml")
BASELINE_TRIPINFO = os.path.join(LUST_BASE, "scenario", "baseline", "dua.static.tripinfo.xml")
BASELINE_SUMMARY = os.path.join(LUST_BASE, "scenario", "baseline", "dua.static.summary.xml")
BASELINE_EDGEDATA = os.path.join(LUST_BASE, "scenario", "baseline", "dua.static.edgedata.xml")
DISASTER_NET = os.path.join(LUST_BASE, "scenario", "disaster", "disaster.lust.net.xml")
DISASTER_TRIPINFO = os.path.join(LUST_BASE, "scenario", "outputs", "disaster.tripinfo.xml")
DISASTER_SUMMARY = os.path.join(LUST_BASE, "scenario", "outputs", "disaster.summary.xml")
DISASTER_EDGEDATA = os.path.join(LUST_BASE, "scenario", "outputs", "disaster.edgedata.xml")

history_output_file = './eq_model_iter_logs/' + datetime.now().strftime("%Y%m%d-%H%M%S") + '/history.json'

if __name__ == "__main__":
    # Static vulnerability
    print("=== Static Vulnerability Assessment ===")
    M_base = compute_throughput(BASELINE_TRIPINFO, BASELINE_SUMMARY)
    R_base = compute_redundancy(BASELINE_NET)
    C_base = compute_entropy(BASELINE_TRIPINFO)
    S_base = compute_stress(BASELINE_NET, BASELINE_EDGEDATA)
    print(f"Baseline: M={M_base:.4f} R={R_base:.4f} C={C_base:.4f} S={S_base:.4f}")

    if not (os.path.exists(DISASTER_TRIPINFO) and os.path.exists(DISASTER_EDGEDATA)):
        sys.exit("Disaster output files missing. Run the disaster simulation first.")

    M_post = compute_throughput(DISASTER_TRIPINFO, DISASTER_SUMMARY)
    R_post = compute_redundancy(DISASTER_NET)
    C_post = compute_entropy(DISASTER_TRIPINFO)
    S_post = compute_stress(DISASTER_NET, DISASTER_EDGEDATA)
    print(f"Post-disaster: M={M_post:.4f} R={R_post:.4f} C={C_post:.4f} S={S_post:.4f}")

    E_base = performance_index_limited({"M": M_base, "R": R_base, "C": C_base, "S": S_base})
    E_post = performance_index_limited({"M": M_post, "R": R_post, "C": C_post, "S": S_post})
    dE = system_vulnerability(E_base, E_post)
    print(f"Static vulnerability ΔE = {dE:.4f}\n")

    print("=== Iterative Equilibrium Learning ===")
    scenario_dir = os.path.join(LUST_BASE, "scenario")
    iter_trip = os.path.join(scenario_dir, "outputs", "iter.tripinfo.xml")
    iter_edge = os.path.join(scenario_dir, "outputs", "iter.edgedata.xml")
    E_final, final_caps, history = eq_model(
        DISASTER_NET, scenario_dir,
        max_iterations=3, learning_rate=0.1, verbose=True
    )
    dE_learned = system_vulnerability(E_base, E_final)
    print(f"\nInitial post-disaster E = {E_post:.4f}")
    print(f"Final (after learning)   E = {E_final:.4f}")
    print(f"Vulnerability after learning ΔE = {dE_learned:.4f}")

    with (open(history_output_file, 'w')) as history_file:
        json.dump(history, history_file)