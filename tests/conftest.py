import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# tests/data/small.net.xml (built with netconvert from 4 nodes):
#   edge  lanes  speed (m/s)
#   AB    2      10
#   AD    1      10
#   BC    1      20
#   BD    1      10
#   DC    1      10
SMALL_NET_EDGES = {"AB": (2, 10.0), "AD": (1, 10.0), "BC": (1, 20.0),
                   "BD": (1, 10.0), "DC": (1, 10.0)}


@pytest.fixture
def small_net():
    return os.path.join(DATA_DIR, "small.net.xml")


def write_edgedata(path, intervals):
    """
    intervals: list of (begin, end, {edge_id: {"entered": n, "speed": v,
    "sampledSeconds": s}}); keys other than entered are optional.
    """
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<meandata>"]
    for begin, end, edges in intervals:
        lines.append(f'  <interval begin="{begin}" end="{end}" id="test">')
        for edge_id, data in edges.items():
            attrs = " ".join(f'{key}="{value}"' for key, value in data.items())
            lines.append(f'    <edge id="{edge_id}" {attrs}/>')
        lines.append("  </interval>")
    lines.append("</meandata>")
    with open(path, "w") as f:
        f.write("\n".join(lines))
    return str(path)


def write_tripinfo(path, arrivals):
    """arrivals: list of arrival times, None for a trip without arrival."""
    lines = ["<tripinfos>"]
    for i, arrival in enumerate(arrivals):
        arrival_attr = f' arrival="{arrival}"' if arrival is not None else ""
        lines.append(f'  <tripinfo id="v{i}" departLane="AB_0" arrivalLane="BC_0"{arrival_attr}/>')
    lines.append("</tripinfos>")
    with open(path, "w") as f:
        f.write("\n".join(lines))
    return str(path)


def write_summary(path, loaded):
    with open(path, "w") as f:
        f.write(f'<summary>\n  <step time="0.00" loaded="1"/>\n'
                f'  <step time="10.00" loaded="{loaded}" arrived="2"/>\n</summary>')
    return str(path)
