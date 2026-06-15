"""
Replication of the Bratislava flood vulnerability assessment (Section 7.1).
Uses the synthetic KPI values from the worked example to compute:
  - Baseline performance index E_base
  - Post‑event performance index E_post
  - System vulnerability ΔE_s
  - Equity vulnerability ΔQ_s
"""

from kpi_functions import performance_index, system_vulnerability

def compute_equity_vulnerability(Q_base: float, Q_post: float) -> float:
    """Equity vulnerability ΔQ_s = (Q_post - Q_base) / (1 - Q_base)."""
    return (Q_post - Q_base) / (1.0 - Q_base) if Q_base < 1.0 else 0.0


def main():
    # Baseline KPIs (from Step 3.2 of the example)
    baseline_kpis = {
        "M": 0.82,   # mobility throughput
        "R": 0.70,   # redundancy ratio
        "C": 0.75,   # network entropy
        "S": 0.35,   # stress index
        "Q": 0.25,   # equity penalty
        "I": 0.68,   # intermodal synergy
    }

    # Post‑event KPIs (from Step 3.4)
    post_kpis = {
        "M": 0.68,
        "R": 0.45,
        "C": 0.62,
        "S": 0.72,
        "Q": 0.48,
        "I": 0.55,
    }

    E_base = performance_index(baseline_kpis)
    E_post = performance_index(post_kpis)

    delta_E_system = system_vulnerability(E_base, E_post)
    delta_Q_equity = compute_equity_vulnerability(
        baseline_kpis["Q"], post_kpis["Q"]
    )

    print("=== Baseline Equilibrium ===")
    print(f"KPIs: {baseline_kpis}")
    print(f"Performance index E_base = {E_base:.4f}\n")

    print("=== Post‑Event Equilibrium ===")
    print(f"KPIs: {post_kpis}")
    print(f"Performance index E_post = {E_post:.4f}\n")

    print("=== Vulnerability Assessment ===")
    print(f"System vulnerability ΔE_s = 1 - {E_post:.4f}/{E_base:.4f} = {delta_E_system:.4f}")
    print(f"  → ({delta_E_system:.2f} : {'Moderate to Major' if delta_E_system >= 0.30 else 'Minor to Moderate'} vulnerability)\n")

    print(f"Equity penalty increase: Q from {baseline_kpis['Q']} to {post_kpis['Q']}")
    print(f"Equity vulnerability ΔQ_s = ({post_kpis['Q']} - {baseline_kpis['Q']}) / (1 - {baseline_kpis['Q']}) = {delta_Q_equity:.4f}")
    print(f"  → ({delta_Q_equity:.2f} : Major equity vulnerability for elderly)")

if __name__ == "__main__":
    main()