import sys
import os

sys.path.append(os.path.dirname(__file__))

from kpi_functions import (
    compute_throughput,
    compute_redundancy,
    compute_entropy,
    compute_stress,
    performance_index_limited,
    system_vulnerability
)

LUST_BASE_DIR = "AntifragiCity-WP4/T4.1-KPIs/LuSTScenario-master"

BASELINE_NET = LUST_BASE_DIR + "/scenario/lust.net.xml"
BASELINE_TRIPINFO = LUST_BASE_DIR + "/scenario/baseline/dua.static.tripinfo.xml"
BASELINE_SUMMARY = LUST_BASE_DIR + "/scenario/baseline/dua.static.summary.xml"
BASELINE_EDGEDATA = LUST_BASE_DIR + "/scenario/baseline/dua.static.edgedata.xml"

DISASTER_NET = LUST_BASE_DIR + "/scenario/lust.net.xml"
DISASTER_TRIPINFO = LUST_BASE_DIR + "/scenario/outputs/disaster.tripinfo.xml"
DISASTER_SUMMARY = LUST_BASE_DIR + "/scenario/outputs/disaster.summary.xml"
DISASTER_EDGEDATA = LUST_BASE_DIR + "/scenario/outputs/disaster.edgedata.xml"

ROUTE_FILES = [
    LUST_BASE_DIR + "/scenario/buslines.rou.xml",
    LUST_BASE_DIR + "/scenario/DUARoutes/local.0.rou.xml",
    LUST_BASE_DIR + "/scenario/DUARoutes/local.1.rou.xml",
    LUST_BASE_DIR + "/scenario/DUARoutes/local.2.rou.xml",
    LUST_BASE_DIR + "/scenario/transit.rou.xml",
]


if __name__ == "__main__":
    print("Computing baseline KPIs …")
    M_base = compute_throughput(BASELINE_TRIPINFO, BASELINE_SUMMARY)
    R_base = compute_redundancy(BASELINE_NET)
    C_base = compute_entropy(BASELINE_TRIPINFO, ROUTE_FILES)
    S_base = compute_stress(BASELINE_NET, BASELINE_EDGEDATA, sim_duration_hours=25.0)

    print(f"Baseline KPIs: M={M_base:.4f}, R={R_base:.4f}, C={C_base:.4f}, S={S_base:.4f}")

    if not (os.path.exists(DISASTER_TRIPINFO) and os.path.exists(DISASTER_EDGEDATA)):
        print("\nDisaster output files not found. Please run a SUMO simulation with the closed edges.")
        print("See instructions at the end of this script.")
        sys.exit(1)

    print("\nComputing post‑disaster KPIs …")
    M_post = compute_throughput(DISASTER_TRIPINFO, DISASTER_SUMMARY)
    R_post = compute_redundancy(DISASTER_NET)
    C_post = compute_entropy(DISASTER_TRIPINFO, ROUTE_FILES)
    S_post = compute_stress(DISASTER_NET, DISASTER_EDGEDATA, sim_duration_hours=25.0)

    print(f"Post‑disaster KPIs: M={M_post:.4f}, R={R_post:.4f}, C={C_post:.4f}, S={S_post:.4f}")

    baseline_kpis = {"M": M_base, "R": R_base, "C": C_base, "S": S_base}
    post_kpis = {"M": M_post, "R": R_post, "C": C_post, "S": S_post}

    # 4. Performance indices
    E_base = performance_index_limited(baseline_kpis)
    E_post = performance_index_limited(post_kpis)
    dE = system_vulnerability(E_base, E_post)

    print("\n=== Vulnerability Assessment ===")
    print(f"System vulnerability ΔE = 1 - {E_post:.4f}/{E_base:.4f} = {dE:.4f}")
