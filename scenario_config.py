"""
Shared paths and settings for the Luxembourg (LuST) experiments.

All paths are absolute (resolved from this file's location), so the scripts
work regardless of the current working directory.
"""
import os

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
LUST_BASE = os.path.join(REPO_ROOT, "AntifragiCity-WP4", "T4.1-KPIs", "LuSTScenario-master")
SCENARIO_DIR = os.path.join(LUST_BASE, "scenario")
OUTPUTS_DIR = os.path.join(SCENARIO_DIR, "outputs")
BASELINE_DIR = os.path.join(SCENARIO_DIR, "baseline")

# --- Network -----------------------------------------------------------------
# Baseline and post-disaster runs use the same network; the disruption is
# applied by the rerouter in disaster_closures.add.xml (loaded by
# disaster.sumocfg).
BASE_NET = os.path.join(SCENARIO_DIR, "lust.net.xml")
BASELINE_NET = BASE_NET
DISASTER_NET = BASE_NET

# --- SUMO configs --------------------------------------------------------------
BASELINE_CFG = "dua.static.sumocfg"   # run via AntifragiCity-WP4/T4.1-KPIs/get_benchmark.py
DISASTER_CFG = "disaster.sumocfg"

# --- Simulation horizon (both runs: 25 h) ---------------------------------------
SIM_BEGIN = 0
SIM_END = 90000

# --- Disruption and KPI windows (seconds) ----------------------------------------
CLOSURE_WINDOW = (61200, 64800)       # 17:00–18:00, see disaster_closures.add.xml
STRESS_WINDOW = CLOSURE_WINDOW        # window over which the stress index S is measured
ENTROPY_WINDOW = None                 # None = whole horizon

# Period (s) of the hourly edgedata outputs needed for windowed KPIs.
HOURLY_PERIOD = 3600

# --- Baseline outputs (written by get_benchmark.py) -----------------------------
BASELINE_TRIPINFO = os.path.join(BASELINE_DIR, "dua.static.tripinfo.xml")
BASELINE_SUMMARY = os.path.join(BASELINE_DIR, "dua.static.summary.xml")
BASELINE_EDGEDATA = os.path.join(BASELINE_DIR, "dua.static.edgedata.xml")
BASELINE_HOURLY_EDGEDATA = os.path.join(BASELINE_DIR, "dua.static.hourly.edgedata.xml")

# --- Post-disaster outputs (written by `sumo -c disaster.sumocfg`) ---------------
DISASTER_TRIPINFO = os.path.join(OUTPUTS_DIR, "disaster.tripinfo.xml")
DISASTER_SUMMARY = os.path.join(OUTPUTS_DIR, "disaster.summary.xml")
DISASTER_EDGEDATA = os.path.join(OUTPUTS_DIR, "disaster.edgedata.xml")
DISASTER_HOURLY_EDGEDATA = os.path.join(OUTPUTS_DIR, "disaster.hourly.edgedata.xml")

# --- Planned demand (only needed for the OD-based entropy variants) --------------
ROUTE_FILES = [
    os.path.join(SCENARIO_DIR, "buslines.rou.xml"),
    os.path.join(SCENARIO_DIR, "DUARoutes", "local.0.rou.xml"),
    os.path.join(SCENARIO_DIR, "DUARoutes", "local.1.rou.xml"),
    os.path.join(SCENARIO_DIR, "DUARoutes", "local.2.rou.xml"),
    os.path.join(SCENARIO_DIR, "transit.rou.xml"),
]

# --- Learning-loop logs ----------------------------------------------------------
EQ_MODEL_LOG_DIR = os.path.join(REPO_ROOT, "eq_model_iter_logs")
