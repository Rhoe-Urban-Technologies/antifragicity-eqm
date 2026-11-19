"""Static vulnerability assessment for the LuST scenario (baseline vs. post-disaster)."""
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import scenario_config as cfg
from kpi_functions import (
    classify_vulnerability,
    compute_link_entropy,
    compute_redundancy,
    compute_stress,
    compute_throughput,
    performance_index_limited,
    system_vulnerability,
)


def compute_kpis(net_file, tripinfo_file, summary_file, edgedata_file, hourly_edgedata_file,
                 stress_window=cfg.STRESS_WINDOW, entropy_window=cfg.ENTROPY_WINDOW):
    """
    The four SUMO-derived core KPIs for one run. Windowed KPIs read the hourly
    edgedata output, whole-horizon KPIs the default edgedata output.
    """
    def edgedata_for(window):
        return hourly_edgedata_file if window is not None else edgedata_file

    return {
        "M": compute_throughput(tripinfo_file, summary_file),
        "R": compute_redundancy(net_file),
        "C": compute_link_entropy(edgedata_for(entropy_window), net_file, window=entropy_window),
        "S": compute_stress(net_file, edgedata_for(stress_window), window=stress_window),
    }


def _require(files, how_to_generate):
    missing = [f for f in files if not os.path.exists(f)]
    if missing:
        sys.exit("Missing simulation outputs:\n  " + "\n  ".join(missing)
                 + f"\n{how_to_generate}")


def static_assessment(stress_window=cfg.STRESS_WINDOW, entropy_window=cfg.ENTROPY_WINDOW):
    """Compute baseline and post-disaster KPIs, E and ΔE. Returns a dict."""
    needs_hourly = stress_window is not None or entropy_window is not None

    baseline_files = [cfg.BASELINE_TRIPINFO, cfg.BASELINE_SUMMARY, cfg.BASELINE_EDGEDATA]
    disaster_files = [cfg.DISASTER_TRIPINFO, cfg.DISASTER_SUMMARY, cfg.DISASTER_EDGEDATA]
    if needs_hourly:
        baseline_files.append(cfg.BASELINE_HOURLY_EDGEDATA)
        disaster_files.append(cfg.DISASTER_HOURLY_EDGEDATA)
    setup_hint = ("\nHourly edgedata also needs the local scenario config changes "
                  "in README §3.3.2." if needs_hourly else "")
    _require(baseline_files, "Run the baseline: `python get_benchmark.py` in "
                             "AntifragiCity-WP4/T4.1-KPIs." + setup_hint)
    _require(disaster_files, "Run the disaster scenario: `sumo -c LuSTScenario-master/scenario/"
                             "disaster.sumocfg` in AntifragiCity-WP4/T4.1-KPIs." + setup_hint)

    print("Computing baseline KPIs …")
    baseline_kpis = compute_kpis(cfg.BASELINE_NET, cfg.BASELINE_TRIPINFO, cfg.BASELINE_SUMMARY,
                                 cfg.BASELINE_EDGEDATA, cfg.BASELINE_HOURLY_EDGEDATA,
                                 stress_window, entropy_window)
    print("Baseline KPIs: " + _fmt(baseline_kpis))

    print("\nComputing post‑disaster KPIs …")
    post_kpis = compute_kpis(cfg.DISASTER_NET, cfg.DISASTER_TRIPINFO, cfg.DISASTER_SUMMARY,
                             cfg.DISASTER_EDGEDATA, cfg.DISASTER_HOURLY_EDGEDATA,
                             stress_window, entropy_window)
    print("Post‑disaster KPIs: " + _fmt(post_kpis))

    E_base = performance_index_limited(baseline_kpis)
    E_post = performance_index_limited(post_kpis)
    dE = system_vulnerability(E_base, E_post)
    cls, label = classify_vulnerability(dE)

    print("\n=== Vulnerability Assessment ===")
    print(f"System vulnerability ΔE = 1 - {E_post:.4f}/{E_base:.4f} = {dE:.4f}"
          f"  → Class {cls} – {label} (D3.2 Table 9)")

    return {"baseline_kpis": baseline_kpis, "post_kpis": post_kpis,
            "E_base": E_base, "E_post": E_post, "delta_E": dE,
            "impact_class": cls, "impact_label": label}


def _fmt(kpis):
    return ", ".join(f"{k}={v:.4f}" for k, v in kpis.items())


if __name__ == "__main__":
    static_assessment()
