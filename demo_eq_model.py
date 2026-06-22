"""Synthetic demo of the equilibrium model with meaningful dynamics."""

import math, copy

# ----------------------------------------------------------------------
# Mock KPI functions that depend on capacities
# We simulate 4 edges: "main1", "main2", "side1", "side2"
# "main" edges carry most traffic; "side" edges are alternatives.
# ----------------------------------------------------------------------
def mock_kpis(caps):
    # caps: dict with keys "main1","main2","side1","side2"
    # Assume total demand is 4000 veh/h.
    # Traffic is distributed: main edges take 60% each of the demand
    # (so overloaded if capacity is low), side edges take 40% each.
    flow_main = 0.6 * 4000 / 2   # 1200 veh/h per main edge
    flow_side = 0.4 * 4000 / 2   #  800 veh/h per side edge

    # Per-edge stress
    stresses = {}
    for eid in caps:
        if "main" in eid:
            flow = flow_main
        else:
            flow = flow_side
        cap = caps[eid]
        volume_ratio = flow / cap if cap > 0 else 1.0
        # Assume free-flow speed 13.9 m/s (50 km/h), actual speed drops linearly with volume ratio
        # actual_speed = free_speed * max(0.2, 1 - 0.5*volume_ratio)  # simplified
        free_speed = 13.9
        actual_speed = free_speed * max(0.2, 1 - 0.5 * volume_ratio)
        speed_drop = max(0.0, 1.0 - actual_speed / free_speed)
        stress = min(1.0, volume_ratio * speed_drop)
        stresses[eid] = stress

    S = sum(stresses.values()) / len(stresses)

    # Mobility throughput M: fraction of demand actually served.
    # If capacity is insufficient, some demand is unserved.
    # Total demand 4000, total capacity sum(caps.values())
    total_cap = sum(caps.values())
    M = min(1.0, total_cap / 4000)  # capped at 1.0

    # Redundancy R: constant for this simple network
    R = 0.7

    # Entropy C: higher if flows are more balanced.
    # We'll compute from the flows: if all equal, entropy=1; if uneven, less.
    # Use actual flows (which are constant here) but could vary.
    flows = [flow_main, flow_main, flow_side, flow_side]
    total_flow = sum(flows)
    if total_flow == 0:
        C = 0.0
    else:
        ent = -sum((f / total_flow) * math.log2(f / total_flow) for f in flows)
        max_ent = math.log2(len(flows))
        C = ent / max_ent

    return M, R, C, S, stresses


def performance_index_limited(kpis):
    M, R, C, S = kpis
    return (M + R + C - S + 1) / 4


def demo_eq_model(max_iterations=30, eta_s=0.1):
    # Initial capacities: main edges have only 1500 (slightly over demand 1200),
    # side edges have 3000 (more than needed).
    caps = {"main1": 1500, "main2": 1500, "side1": 3000, "side2": 3000}
    base_caps = copy.deepcopy(caps)
    rho_infra = 0.0
    dec = math.exp(-1 / 30)
    prev_E = None
    stability = 0

    print("Synthetic EQ Model Demo (2 main + 2 side edges)")
    print("Initial capacities (veh/h):", caps)
    print(f"{'Iteration':<5} {'E':>8} {'M':>6} {'R':>6} {'C':>6} {'S':>6} {'main1':>7} {'main2':>7} {'side1':>7} {'side2':>7}")
    for iteration in range(1, max_iterations + 1):
        M, R, C, S, edge_stresses = mock_kpis(caps)
        E = performance_index_limited((M, R, C, S))

        print(f"{iteration:<5} {E:8.4f} {M:6.4f} {R:6.4f} {C:6.4f} {S:6.4f} "
              f"{caps['main1']:7.1f} {caps['main2']:7.1f} {caps['side1']:7.1f} {caps['side2']:7.1f}")

        # Update SRI components
        rho_infra = dec * rho_infra + S
        eps_ops = (R * (1 - S)) / (1 + math.exp(-10 * (R - 0.2)))
        SRI = 0.5 * eps_ops + 0.5 * rho_infra

        # Compute short-term gradient per edge
        avg_stress = S
        g_short = {}
        for eid, stress in edge_stresses.items():
            dev = stress - avg_stress   # positive if edge is more stressed than average
            g_short[eid] = eta_s * dev * SRI

        # Update capacities
        new_caps = {}
        for eid, cap in caps.items():
            adj = g_short.get(eid, 0.0)
            new_cap = cap * (1 + adj)
            # Keep within 0.5-2.0 times base capacity
            base = base_caps[eid]
            new_cap = max(0.5 * base, min(2.0 * base, new_cap))
            new_caps[eid] = new_cap
        caps = new_caps

        # Convergence check
        if prev_E is not None and abs(E - prev_E) < 1e-4:
            stability += 1
            if stability >= 3:
                print(f"Converged after {iteration} iterations.")
                break
        else:
            stability = 0
        prev_E = E

    print(f"\nFinal performance index: {E:.4f}")
    print("Final capacities:", {k: f"{v:.1f}" for k, v in caps.items()})


if __name__ == "__main__":
    demo_eq_model()