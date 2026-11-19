import math

import pytest

import kpi_functions as k
from conftest import SMALL_NET_EDGES, write_edgedata, write_summary, write_tripinfo


# ---------------------------------------------------------------------------
# Bratislava worked example (D3.2 §7.1) and impact classes (Table 9)
# ---------------------------------------------------------------------------

BRATISLAVA_BASE = {"M": 0.82, "R": 0.70, "C": 0.75, "S": 0.35, "Q": 0.25, "I": 0.68}
BRATISLAVA_POST = {"M": 0.68, "R": 0.45, "C": 0.62, "S": 0.72, "Q": 0.48, "I": 0.55}


def test_bratislava_golden_values():
    E_base = k.performance_index(BRATISLAVA_BASE)
    E_post = k.performance_index(BRATISLAVA_POST)
    dE = k.system_vulnerability(E_base, E_post)
    assert E_base == pytest.approx(0.725)
    assert E_post == pytest.approx(0.51667, abs=1e-5)
    assert dE == pytest.approx(0.2874, abs=1e-4)
    assert k.classify_vulnerability(dE) == (3, "Moderate")
    dQ = (BRATISLAVA_POST["Q"] - BRATISLAVA_BASE["Q"]) / (1 - BRATISLAVA_BASE["Q"])
    assert k.classify_vulnerability(dQ) == (4, "Major")


@pytest.mark.parametrize("delta, expected", [
    (-0.1, 0), (0.0, 0), (0.01, 1), (0.0499, 1), (0.05, 2), (0.15, 2),
    (0.1501, 3), (0.30, 3), (0.31, 4), (0.50, 4), (0.51, 5),
])
def test_classify_vulnerability_boundaries(delta, expected):
    assert k.classify_vulnerability(delta)[0] == expected


def test_performance_index_limited_range():
    assert k.performance_index_limited({"M": 1, "R": 1, "C": 1, "S": 0}) == 1.0
    assert k.performance_index_limited({"M": 0, "R": 0, "C": 0, "S": 1}) == 0.0


# ---------------------------------------------------------------------------
# Throughput M
# ---------------------------------------------------------------------------

def test_throughput_uses_loaded_demand_from_summary(tmp_path):
    tripinfo = write_tripinfo(tmp_path / "x.tripinfo.xml", [10.0, 20.0, None])
    summary = write_summary(tmp_path / "s.xml", loaded=4)
    assert k.compute_throughput(tripinfo, summary) == pytest.approx(2 / 4)


def test_throughput_infers_sibling_summary(tmp_path):
    tripinfo = write_tripinfo(tmp_path / "run.tripinfo.xml", [10.0, None])
    write_summary(tmp_path / "run.summary.xml", loaded=5)
    assert k.compute_throughput(tripinfo) == pytest.approx(1 / 5)


def test_throughput_without_summary_falls_back_to_tripinfo(tmp_path):
    tripinfo = write_tripinfo(tmp_path / "trips.xml", [10.0, 20.0, None])
    assert k.compute_throughput(tripinfo) == pytest.approx(2 / 3)


# ---------------------------------------------------------------------------
# Redundancy R
# ---------------------------------------------------------------------------

def test_redundancy_is_reproducible(small_net):
    assert k.compute_redundancy(small_net, sample_size=50) == \
        k.compute_redundancy(small_net, sample_size=50)


def test_od_sample_independent_of_node_order():
    import networkx as nx
    G1 = nx.DiGraph()
    G1.add_nodes_from(range(30))
    G2 = nx.DiGraph()
    G2.add_nodes_from(reversed(range(30)))
    assert k._sample_od_pairs(G1, 40) == k._sample_od_pairs(G2, 40)


# ---------------------------------------------------------------------------
# Edgedata aggregation (B10, windows)
# ---------------------------------------------------------------------------

def test_aggregate_sums_flows_and_weights_speeds(tmp_path):
    path = write_edgedata(tmp_path / "e.xml", [
        (0, 3600, {"AB": {"entered": 10, "speed": 10.0, "sampledSeconds": 100}}),
        (3600, 7200, {"AB": {"entered": 30, "speed": 4.0, "sampledSeconds": 300},
                      ":internal": {"entered": 99}}),
    ])
    flows, speeds, hours = k.aggregate_edgedata(path)
    assert flows == {"AB": 40}
    # sampledSeconds-weighted: (10*100 + 4*300) / 400 = 5.5 (not the last value, 4.0)
    assert speeds["AB"] == pytest.approx(5.5)
    assert hours == pytest.approx(2.0)


def test_aggregate_window_selects_intervals(tmp_path):
    path = write_edgedata(tmp_path / "e.xml", [
        (0, 3600, {"AB": {"entered": 10}}),
        (3600, 7200, {"AB": {"entered": 30}}),
    ])
    flows, _, hours = k.aggregate_edgedata(path, window=(3600, 7200))
    assert flows == {"AB": 30}
    assert hours == pytest.approx(1.0)


def test_aggregate_window_must_be_covered(tmp_path):
    path = write_edgedata(tmp_path / "e.xml", [(0, 7200, {"AB": {"entered": 10}})])
    with pytest.raises(ValueError, match="do not cover"):
        k.aggregate_edgedata(path, window=(3600, 7200))


# ---------------------------------------------------------------------------
# Link entropy C (D3.2 Eq. 17)
# ---------------------------------------------------------------------------

def test_link_entropy_uniform_is_one(tmp_path, small_net):
    path = write_edgedata(tmp_path / "e.xml",
                          [(0, 3600, {eid: {"entered": 7} for eid in SMALL_NET_EDGES})])
    assert k.compute_link_entropy(path, small_net) == pytest.approx(1.0)


def test_link_entropy_single_link_is_zero(tmp_path, small_net):
    path = write_edgedata(tmp_path / "e.xml", [(0, 3600, {"AB": {"entered": 50}})])
    assert k.compute_link_entropy(path, small_net) == pytest.approx(0.0)


def test_link_entropy_counts_links_without_flow(tmp_path, small_net):
    # Two of five links carry equal flow: H = log 2, N = 5
    path = write_edgedata(tmp_path / "e.xml",
                          [(0, 3600, {"AB": {"entered": 5}, "BC": {"entered": 5}})])
    assert k.compute_link_entropy(path, small_net) == pytest.approx(math.log(2) / math.log(5))


# ---------------------------------------------------------------------------
# Stress S
# ---------------------------------------------------------------------------

def _stress_edgedata(tmp_path):
    # 1 h interval. AB: 1800 veh/h on 2 lanes (v/c = 0.5), speed 5 of 10 → drop 0.5.
    # BC: 900 veh/h on 1 lane (v/c = 0.5), speed 10 of 20 → drop 0.5. Others empty.
    return write_edgedata(tmp_path / "e.xml", [(0, 3600, {
        "AB": {"entered": 1800, "speed": 5.0, "sampledSeconds": 10},
        "BC": {"entered": 900, "speed": 10.0, "sampledSeconds": 10},
    })])


def test_edge_stresses_formula(tmp_path, small_net):
    stresses = k.compute_edge_stresses(small_net, _stress_edgedata(tmp_path))
    assert stresses["AB"] == pytest.approx(0.25)
    assert stresses["BC"] == pytest.approx(0.25)
    assert stresses["AD"] == 0.0
    assert set(stresses) == set(SMALL_NET_EDGES)


def test_stress_is_mean_of_edge_stresses(tmp_path, small_net):
    assert k.compute_stress(small_net, _stress_edgedata(tmp_path)) == pytest.approx(0.5 / 5)


def test_stress_period_comes_from_edgedata(tmp_path, small_net):
    # Same counts spread over 2 h halve the hourly flow, so stress halves.
    path = write_edgedata(tmp_path / "e2.xml", [(0, 7200, {
        "AB": {"entered": 1800, "speed": 5.0, "sampledSeconds": 10}})])
    assert k.compute_edge_stresses(small_net, path)["AB"] == pytest.approx(0.125)


def test_stress_uses_supplied_capacities(tmp_path, small_net):
    # Doubling AB's capacity halves its v/c ratio.
    stresses = k.compute_edge_stresses(small_net, _stress_edgedata(tmp_path),
                                       capacities={"AB": 7200.0})
    assert stresses["AB"] == pytest.approx(0.125)


def test_stress_window(tmp_path, small_net):
    path = write_edgedata(tmp_path / "e.xml", [
        (0, 3600, {"AB": {"entered": 0}}),
        (3600, 7200, {"AB": {"entered": 1800, "speed": 5.0, "sampledSeconds": 10}}),
    ])
    stresses = k.compute_edge_stresses(small_net, path, window=(3600, 7200))
    assert stresses["AB"] == pytest.approx(0.25)


def test_stress_options_are_keyword_only(tmp_path, small_net):
    # Regression for the bug where the period length was passed positionally
    # and ended up as capacity_per_lane.
    with pytest.raises(TypeError):
        k.compute_stress(small_net, _stress_edgedata(tmp_path), 25.0)


def test_stress_rejects_window_and_duration_together(tmp_path, small_net):
    with pytest.raises(ValueError):
        k.compute_stress(small_net, _stress_edgedata(tmp_path),
                         window=(0, 3600), sim_duration_hours=1.0)
