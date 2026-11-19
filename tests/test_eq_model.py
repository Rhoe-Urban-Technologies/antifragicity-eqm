import os
import xml.etree.ElementTree as ET

import pytest

import eq_model as eq
import kpi_functions as k
from conftest import write_edgedata, write_summary, write_tripinfo


def _lane_speeds(net_file):
    root = ET.parse(net_file).getroot()
    return {edge.get("id"): float(edge.find("lane").get("speed"))
            for edge in root.findall("edge") if not edge.get("id").startswith(":")}


def test_apply_capacities_caps_speed_factor(tmp_path, small_net):
    out = str(tmp_path / "iter.net.xml")
    base = eq.get_initial_capacities(small_net)
    eq.apply_capacities_to_network(small_net, out,
                                   {"AB": base["AB"] * 2.0, "BC": base["BC"] * 0.5},
                                   max_speed_factor=1.3)
    speeds = _lane_speeds(out)
    assert speeds["AB"] == pytest.approx(10.0 * 1.3)   # capped, not 2×
    assert speeds["BC"] == pytest.approx(20.0 * 0.5)   # reductions applied in full
    assert speeds["AD"] == pytest.approx(10.0)


def _fake_sumo(network_file, tripinfo_rel, edgedata_rel, scenario_dir, **kwargs):
    """Stand-in for a SUMO run: AB is congested in the second hour."""
    out = lambda rel: os.path.join(scenario_dir, rel)
    write_tripinfo(out(tripinfo_rel), [10.0, 20.0, None])
    write_summary(out(kwargs["summary_output_rel"]), loaded=3)
    busy = {"AB": {"entered": 1800, "speed": 5.0, "sampledSeconds": 10},
            "BC": {"entered": 100, "speed": 19.0, "sampledSeconds": 10}}
    write_edgedata(out(edgedata_rel), [(0, 7200, busy)])
    write_edgedata(out(kwargs["hourly_edgedata_rel"]),
                   [(0, 3600, {}), (3600, 7200, busy)])


def test_eq_model_loop(tmp_path, small_net, monkeypatch):
    monkeypatch.setattr(eq, "run_sumo_simulation", _fake_sumo)
    window = (3600, 7200)

    E_final, caps, history = eq.eq_model(small_net, str(tmp_path), max_iterations=3,
                                         verbose=False, begin_time=0, end_time=7200,
                                         stress_window=window)

    assert [h["day"] for h in history] == [1, 2, 3, "final"]
    assert history[-1]["E"] == pytest.approx(E_final)

    # Day 1 uses base capacities, so S must equal the static computation.
    hourly = os.path.join(tmp_path, "outputs", "iter.hourly.edgedata.xml")
    assert history[0]["S"] == pytest.approx(k.compute_stress(small_net, hourly, window=window))
    assert history[0]["capacity_factor_mean"] == pytest.approx(1.0)

    # The congested edge gains capacity, the others lose some; all stay in bounds.
    base = eq.get_initial_capacities(small_net)
    assert caps["AB"] > base["AB"]
    assert caps["AD"] < base["AD"]
    for eid, cap in caps.items():
        assert 0.5 * base[eid] <= cap <= 2.0 * base[eid]

    # Redundancy is computed once (topology does not change).
    assert len({h["R"] for h in history}) == 1
