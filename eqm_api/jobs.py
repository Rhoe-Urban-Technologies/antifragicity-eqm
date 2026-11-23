"""Jobs: a spec saved on disk and the existing script run as a local background process.

Three kinds share one queue, because they all read or write the same files in the scenario folder:

- simulation: the baseline (`get_benchmark.py`) or post-disaster (`sumo -c disaster.sumocfg`) SUMO run. It writes
  the outputs the assessment and the EQ runs read (see scenario_config.py), replacing the previous ones;
- assessment: the static vulnerability assessment (`luxembourg_example.py`) on those outputs;
- eqm: the static assessment, then the EQ learning loop (`luxembourg_example_eq_model.py`).

The specs (`SimulationCreate`, `AssessmentCreate`, `EqmRunCreate`) are the contract an orchestrator can send later,
and the folder layout maps onto tables (simulations, assessments, eqm_runs, eqm_days) if jobs move to a database.

    outputs/jobs/<id>/job.json       the job record (kind, status, spec, timings)
    outputs/jobs/<id>/job.log        script and SUMO output
    outputs/jobs/<id>/settings.json  eqm: the settings passed to the script (--settings)
    outputs/jobs/<id>/run/           assessment: result.json; eqm: run.json (rewritten every day), capacities.json

Jobs run in their own process group, so they survive an API reload; a restarted API adopts running jobs by PID and
settles their status from their output when they end.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import threading
import uuid
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

import scenario_config as cfg
from eqm_api import settings
from luxembourg_example_eq_model import EQ_SETTINGS
from sumo_runner import find_sumo_binary

Status = Literal['queued', 'running', 'completed', 'failed', 'cancelled']
Kind = Literal['simulation', 'assessment', 'eqm']
Window = tuple[int, int] | None

#: SUMO's step log, e.g. "Step #1234.00 (1ms ~= 1000.00*RT, ...)".
STEP = re.compile(r'Step #(\d+(?:\.\d+)?)')

#: The files each simulation writes (scenario_config.py), checked to decide whether it completed.
SIMULATION_OUTPUTS = {
    'baseline': [cfg.BASELINE_TRIPINFO, cfg.BASELINE_SUMMARY, cfg.BASELINE_EDGEDATA, cfg.BASELINE_HOURLY_EDGEDATA],
    'disaster': [cfg.DISASTER_TRIPINFO, cfg.DISASTER_SUMMARY, cfg.DISASTER_EDGEDATA, cfg.DISASTER_HOURLY_EDGEDATA],
}


def check_window(window: Window, begin: int, end: int) -> Window:
    """Windowed KPIs read hourly edgedata, so a window must cover whole hours of the simulated horizon."""
    if window is None:
        return None
    b, e = window
    if not begin <= b < e <= end:
        raise ValueError(f'The window must lie within the simulated horizon {begin}–{end} s, with begin < end.')
    if (b - begin) % cfg.HOURLY_PERIOD or (e - begin) % cfg.HOURLY_PERIOD:
        raise ValueError(f'Window bounds must be whole hours ({cfg.HOURLY_PERIOD} s) from the start of the run.')
    return window


class SimulationCreate(BaseModel):
    """Run the baseline or the post-disaster simulation (25 h). Replaces that scenario's current outputs."""
    scenario: Literal['baseline', 'disaster']
    name: str | None = Field(None, max_length=80)


class AssessmentCreate(BaseModel):
    """The static assessment (D3.2): KPIs of the baseline and disaster outputs, E, ΔE and the impact class."""
    name: str | None = Field(None, max_length=80)
    stress_window: Window = cfg.STRESS_WINDOW
    entropy_window: Window = cfg.ENTROPY_WINDOW

    @field_validator('stress_window', 'entropy_window')
    @classmethod
    def whole_hours(cls, v: Window) -> Window:
        return check_window(v, cfg.SIM_BEGIN, cfg.SIM_END)


class EqmSettings(BaseModel):
    """The learning-loop settings. Defaults are EQ_SETTINGS in luxembourg_example_eq_model.py."""
    max_iterations: int = Field(EQ_SETTINGS['max_iterations'], ge=1, le=200)
    learning_rate: float = Field(EQ_SETTINGS['learning_rate'], gt=0, le=10)
    memory_decay: float = Field(EQ_SETTINGS['memory_decay'], ge=0, lt=1)
    capacity_min_factor: float = Field(EQ_SETTINGS['capacity_min_factor'], gt=0, le=1)
    capacity_max_factor: float = Field(EQ_SETTINGS['capacity_max_factor'], ge=1, le=10)
    max_speed_factor: float = Field(EQ_SETTINGS['max_speed_factor'], ge=1, le=5)
    convergence_tol: float = Field(EQ_SETTINGS['convergence_tol'], gt=0, le=1)
    begin_time: int = Field(EQ_SETTINGS['begin_time'], ge=0)
    end_time: int = Field(EQ_SETTINGS['end_time'], gt=0, le=7 * 86400)
    stress_window: Window = EQ_SETTINGS['stress_window']
    entropy_window: Window = EQ_SETTINGS['entropy_window']

    @model_validator(mode='after')
    def consistent(self) -> 'EqmSettings':
        if self.end_time <= self.begin_time:
            raise ValueError('end_time must be after begin_time.')
        check_window(self.stress_window, self.begin_time, self.end_time)
        check_window(self.entropy_window, self.begin_time, self.end_time)
        return self


class EqmRunCreate(BaseModel):
    """An EQ learning run. The static assessment runs first and needs both simulations' outputs."""
    name: str = Field(min_length=1, max_length=80)
    settings: EqmSettings = Field(default_factory=EqmSettings)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _write_json(path: Path, data) -> None:
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    tmp.replace(path)  # readers never see half a file


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _seconds_since(iso: str | None) -> float | None:
    if not iso:
        return None
    return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds()


def _window_arg(window) -> str:
    return 'none' if window is None else f'{window[0]},{window[1]}'


class JobStore:
    def __init__(self, root: Path = settings.JOBS_DIR):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.processes: dict[str, subprocess.Popen] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # records ---------------------------------------------------------------------------------------------------

    def folder(self, job_id: str) -> Path:
        return self.root / job_id

    def get(self, job_id: str) -> dict | None:
        path = self.folder(job_id) / 'job.json'
        if not job_id.replace('-', '').isalnum() or not path.is_file():
            return None
        return json.loads(path.read_text(encoding='utf-8'))

    def save(self, job: dict) -> dict:
        with self.lock:
            job['updated_at'] = now()
            _write_json(self.folder(job['_id']) / 'job.json', job)
        return job

    def all(self, kind: Kind | None = None) -> list[dict]:
        if not self.root.is_dir():  # deleted while the API runs (tests clean up)
            return []
        jobs = [self.get(p.name) for p in self.root.iterdir() if (p / 'job.json').is_file()]
        return sorted((j for j in jobs if j and (kind is None or j['kind'] == kind)),
                      key=lambda j: j['created_at'], reverse=True)

    def create(self, kind: Kind, name: str, spec: dict) -> dict:
        job_id = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:6]
        folder = self.folder(job_id)
        folder.mkdir(parents=True)
        if kind == 'eqm':
            _write_json(folder / 'settings.json', spec['settings'])
        job = dict(_id=job_id, kind=kind, name=name, status='queued', created_at=now(), started_at=None,
                   finished_at=None, spec=spec, pid=None, return_code=None, error=None, cancel_requested=False)
        return self.save(job)

    # results ---------------------------------------------------------------------------------------------------

    def assessment_result(self, job_id: str) -> dict | None:
        return _read_json(self.folder(job_id) / 'run' / 'result.json')

    def eqm_record(self, job_id: str) -> dict | None:
        return _read_json(self.folder(job_id) / 'run' / 'run.json')

    def eqm_capacities(self, job_id: str) -> dict | None:
        return _read_json(self.folder(job_id) / 'run' / 'capacities.json')

    # progress --------------------------------------------------------------------------------------------------

    def log_tail(self, job_id: str, lines: int = 200) -> list[str]:
        path = self.folder(job_id) / 'job.log'
        if not path.is_file():
            return []
        with path.open('rb') as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 256 * 1024))
            return f.read().decode('utf-8', 'replace').splitlines()[-lines:]

    def simulated_time(self, job_id: str) -> float | None:
        """The last simulated second in SUMO's step log, or None before the simulation starts."""
        for line in reversed(self.log_tail(job_id, 2000)):
            match = STEP.search(line)
            if match:
                return float(match.group(1))
        return None

    def progress(self, job: dict) -> dict:
        running = job['status'] == 'running'
        if job['kind'] == 'simulation':
            total = cfg.SIM_END - cfg.SIM_BEGIN
            t = self.simulated_time(job['_id']) if running else None
            fraction = 1.0 if job['status'] == 'completed' else (
                min(1.0, (t - cfg.SIM_BEGIN) / total) if t is not None else 0.0)
            elapsed = _seconds_since(job['started_at']) if running else None
            eta = elapsed * (1 - fraction) / fraction if elapsed and fraction > 0.01 else None
            return dict(simulated_seconds=None if t is None else t - cfg.SIM_BEGIN, simulated_total=total,
                        fraction=fraction, eta_seconds=eta)
        if job['kind'] == 'assessment':
            return dict(fraction=1.0 if job['status'] == 'completed' else 0.0, eta_seconds=None)

        # eqm: the static assessment, then max_iterations days (fewer if it converges) and a final run
        s = job['spec']['settings']
        record = self.eqm_record(job['_id']) or {}
        history = record.get('history', [])
        days = sum(1 for h in history if h.get('day') != 'final')
        total = s['max_iterations'] + 1
        done = len(history)
        stage = None
        if running:
            stage = 'static' if 'static' not in record else 'learning'
        current = None
        if stage == 'learning':
            t = self.simulated_time(job['_id'])
            if t is not None:
                current = dict(simulation=done + 1, fraction=min(1.0, max(0.0, (t - s['begin_time']) /
                                                                          (s['end_time'] - s['begin_time']))))
        seconds = record.get('day_seconds') or []
        eta = sum(seconds) / len(seconds) * (total - done) if seconds and running else None
        return dict(stage=stage, simulations_done=done, simulations_total=total, days_done=days,
                    current=current, fraction=1.0 if job['status'] == 'completed' else done / total,
                    eta_seconds=eta)

    # running ---------------------------------------------------------------------------------------------------

    def command(self, job: dict) -> tuple[list[str], Path]:
        """The process to run and its working directory."""
        folder = self.folder(job['_id'])
        if job['kind'] == 'simulation':
            if job['spec']['scenario'] == 'baseline':
                return [settings.PYTHON, '-B', 'get_benchmark.py'], settings.BENCHMARK_DIR
            return [find_sumo_binary(), '-c', os.path.join(cfg.SCENARIO_DIR, cfg.DISASTER_CFG)], settings.ROOT
        if job['kind'] == 'assessment':
            spec = job['spec']
            return [settings.PYTHON, '-B', 'luxembourg_example.py',
                    '--stress-window', _window_arg(spec['stress_window']),
                    '--entropy-window', _window_arg(spec['entropy_window']),
                    '--output', str(folder / 'run' / 'result.json')], settings.ROOT
        return [settings.PYTHON, '-B', 'luxembourg_example_eq_model.py', '--settings', str(folder / 'settings.json'),
                '--output', str(folder / 'run')], settings.ROOT

    def _start(self, job: dict) -> None:
        folder = self.folder(job['_id'])
        (folder / 'run').mkdir(exist_ok=True)
        log = (folder / 'job.log').open('ab')
        try:
            cmd, cwd = self.command(job)
            log.write(f'$ {" ".join(cmd)}\n'.encode())
            log.flush()
            process = subprocess.Popen(cmd, cwd=cwd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                       start_new_session=True, env={**os.environ, 'PYTHONUNBUFFERED': '1'})
        except OSError as e:  # includes FileNotFoundError when SUMO is not installed
            job.update(status='failed', finished_at=now(), error=f'Could not start the process: {e}')
            self.save(job)
            return
        finally:
            log.close()
        self.processes[job['_id']] = process
        job.update(status='running', started_at=now(), pid=process.pid, command=cmd)
        self.save(job)

    def _finished(self, job: dict) -> bool:
        process = self.processes.get(job['_id'])
        if process is not None:
            code = process.poll()
            if code is None:
                return False
            job['return_code'] = code
            del self.processes[job['_id']]
            return True
        return not _alive(job.get('pid'))  # adopted from an earlier API process

    def _succeeded(self, job: dict) -> bool:
        if job['kind'] == 'simulation':
            # get_benchmark.py does not pass on SUMO's exit code, so check that the outputs were rewritten
            started = datetime.fromisoformat(job['started_at']).timestamp()
            return all(os.path.isfile(f) and os.path.getmtime(f) >= started - 1
                       for f in SIMULATION_OUTPUTS[job['spec']['scenario']])
        if job['kind'] == 'assessment':
            return self.assessment_result(job['_id']) is not None
        return (self.eqm_record(job['_id']) or {}).get('status') == 'finished'

    def _settle(self, job: dict) -> None:
        if job.get('cancel_requested'):
            job['status'] = 'cancelled'
        elif self._succeeded(job) and job.get('return_code') in (0, None):
            job['status'] = 'completed'
        else:
            tail = [l for l in self.log_tail(job['_id'], 40) if l.strip()]
            errors = [l for l in tail if 'Error' in l or 'error:' in l or 'Missing' in l]
            job['status'] = 'failed'
            job['error'] = (errors[-1] if errors else tail[-1] if tail else 'The process stopped.') + ' (see the log)'
        job['finished_at'] = now()
        self.save(job)

    def tick(self) -> None:
        with self.lock:
            jobs = self.all()
            for job in jobs:
                if job['status'] == 'running' and self._finished(job):
                    self._settle(job)
            running = sum(1 for j in self.all() if j['status'] == 'running')
            for job in sorted((j for j in jobs if j['status'] == 'queued'), key=lambda j: j['created_at']):
                if running >= settings.MAX_RUNNING_JOBS:
                    break
                self._start(job)
                running += 1

    def cancel(self, job_id: str) -> dict | None:
        with self.lock:
            job = self.get(job_id)
            if job is None or job['status'] not in ('queued', 'running'):
                return job
            job['cancel_requested'] = True
            if job['status'] == 'queued':
                job.update(status='cancelled', finished_at=now())
            elif job.get('pid'):
                try:  # the script and its SUMO processes share the group
                    os.killpg(job['pid'], signal.SIGTERM)
                except ProcessLookupError:
                    pass
            return self.save(job)

    def start(self) -> None:
        def loop():
            while not self._stop.wait(2):
                try:
                    self.tick()
                except Exception as e:  # keep the runner alive; the API reports per-job errors
                    print(f'[eqm jobs] {e!r}', flush=True)
        self.tick()
        self._thread = threading.Thread(target=loop, name='eqm-job-runner', daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


def public(job: dict, store: JobStore) -> dict:
    """The job as the API returns it."""
    out = {k: v for k, v in job.items() if k not in ('pid', 'cancel_requested', 'command')}
    out['progress'] = store.progress(job)
    return out
