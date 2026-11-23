"""Paths and limits. Everything can be overridden with EQM_* environment variables."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
#: The folder of get_benchmark.py, which runs the baseline simulation.
BENCHMARK_DIR = ROOT / 'AntifragiCity-WP4' / 'T4.1-KPIs'

#: Where jobs (record, log, settings, outputs) are kept. Must be outside the scenario folder.
JOBS_DIR = Path(os.environ.get('EQM_JOBS_DIR', ROOT / 'outputs' / 'jobs')).resolve()
#: The interpreter that runs the scripts: the one running this API unless told otherwise.
PYTHON = os.environ.get('EQM_PYTHON', sys.executable)
#: Jobs that may run at once. Simulations, assessments and EQ runs read and write the same files in the scenario
#: folder (the baseline/disaster outputs, outputs/iter.*), so keep this at 1 unless that changes.
MAX_RUNNING_JOBS = int(os.environ.get('EQM_MAX_RUNNING_JOBS', '1'))
#: Browser origins allowed to call the API directly.
CORS_ORIGINS = [o for o in os.environ.get('EQM_CORS_ORIGINS', 'http://localhost:5180').split(',') if o]
