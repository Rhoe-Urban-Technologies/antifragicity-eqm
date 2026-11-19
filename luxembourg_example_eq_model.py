"""Luxembourg vulnerability assessment + iterative equilibrium model."""
import json
import math
import os
import subprocess
import sys
from datetime import datetime

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import scenario_config as cfg
from eq_model import eq_model
from kpi_functions import classify_vulnerability, system_vulnerability
from luxembourg_example import static_assessment
from sumo_runner import find_sumo_binary

# Every setting that affects the learning loop; stored with each run.
EQ_SETTINGS = {
    "max_iterations": 30,
    "learning_rate": 0.1,
    "memory_decay": math.exp(-1 / 30),
    "capacity_min_factor": 0.5,
    "capacity_max_factor": 2.0,
    "max_speed_factor": 1.3,
    "convergence_tol": 1e-4,
    "begin_time": cfg.SIM_BEGIN,
    "end_time": cfg.SIM_END,
    "base_cfg": cfg.DISASTER_CFG,
    "stress_window": cfg.STRESS_WINDOW,
    "entropy_window": cfg.ENTROPY_WINDOW,
}


def _git_state(path):
    """Commit hash and whether the working tree has uncommitted changes."""
    try:
        commit = subprocess.run(["git", "-C", path, "rev-parse", "HEAD"],
                                capture_output=True, text=True, check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "-C", path, "status", "--porcelain"],
                                    capture_output=True, text=True, check=True).stdout.strip())
        return {"commit": commit, "dirty": dirty}
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "dirty": None}


def _sumo_version():
    try:
        output = subprocess.run([find_sumo_binary(), "--version"],
                                capture_output=True, text=True).stdout
        return output.splitlines()[0] if output else None
    except (OSError, FileNotFoundError):
        return None


def _write_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


if __name__ == "__main__":
    run_dir = os.path.join(cfg.EQ_MODEL_LOG_DIR, datetime.now().strftime("%Y%m%d-%H%M%S"))
    # Created up front so a long SUMO run can't fail at the very end.
    os.makedirs(run_dir, exist_ok=True)
    run_file = os.path.join(run_dir, "run.json")

    record = {
        "created": datetime.now().isoformat(timespec="seconds"),
        "status": "running",
        "settings": {**EQ_SETTINGS,
                     "disaster_network_file": os.path.relpath(cfg.DISASTER_NET, cfg.REPO_ROOT)},
        "provenance": {
            "repo": _git_state(cfg.REPO_ROOT),
            "submodule": _git_state(os.path.join(cfg.REPO_ROOT, "AntifragiCity-WP4")),
            "sumo_version": _sumo_version(),
        },
    }

    print("=== Static Vulnerability Assessment ===")
    record["static"] = static_assessment(stress_window=cfg.STRESS_WINDOW,
                                         entropy_window=cfg.ENTROPY_WINDOW)
    _write_json(run_file, record)

    print("\n=== Iterative Equilibrium Learning ===")
    E_final, final_caps, history = eq_model(cfg.DISASTER_NET, cfg.SCENARIO_DIR,
                                            verbose=True, **EQ_SETTINGS)
    E_base, E_post = record["static"]["E_base"], record["static"]["E_post"]
    dE_learned = system_vulnerability(E_base, E_final)
    cls, label = classify_vulnerability(dE_learned)

    print(f"\nInitial post-disaster E = {E_post:.4f}")
    print(f"Final (after learning)   E = {E_final:.4f}")
    print(f"Vulnerability after learning ΔE = {dE_learned:.4f}  → Class {cls} – {label}")

    record.update({
        "status": "finished",
        "history": history,
        "final": {"E": E_final, "delta_E": dE_learned,
                  "impact_class": cls, "impact_label": label},
    })
    _write_json(run_file, record)
    _write_json(os.path.join(run_dir, "capacities.json"), final_caps)
    print(f"\nRun record written to {run_dir}")
