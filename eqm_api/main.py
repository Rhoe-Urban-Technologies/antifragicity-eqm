"""FastAPI app for the Antifragicity equilibrium model (EQM): KPIs, vulnerability, simulations and EQ learning runs.

Run from the repository root:  uvicorn eqm_api.main:app --port 8000
Conventions follow @rhoe/data: lists are {items, total}, ids are `_id` strings, 422s use FastAPI's shape
or {"code", "message"} for errors a UI explains.
"""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from functools import lru_cache
import math
import os
import xml.etree.ElementTree as ET
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, Field

import scenario_config as cfg
from eq_model import get_initial_capacities
from eqm_api import settings
from eqm_api.jobs import (SIMULATION_OUTPUTS, AssessmentCreate, EqmRunCreate, JobStore, SimulationCreate, Status,
                          public)
from kpi_functions import (IMPACT_CLASSES, classify_vulnerability, performance_index, performance_index_limited,
                           system_vulnerability)


@lru_cache(maxsize=1)
def base_capacities() -> dict[str, float]:
    """Nominal capacity (veh/h) of every non-internal edge of the scenario network."""
    return get_initial_capacities(cfg.DISASTER_NET)


@lru_cache(maxsize=1)
def closed_edges() -> list[str]:
    """The edges the disaster rerouter closes (disaster_closures.add.xml)."""
    path = os.path.join(cfg.SCENARIO_DIR, 'disaster_closures.add.xml')
    if not os.path.isfile(path):
        return []
    return sorted({e.get('id') for e in ET.parse(path).getroot().iter('closingReroute')})


store = JobStore()


@asynccontextmanager
async def lifespan(app: FastAPI):
    base_capacities()  # parse the network once at start-up, not on the first request
    store.start()
    yield
    store.stop()


app = FastAPI(title='Antifragicity EQM API', version='0.1.0', lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=2048)
app.add_middleware(CORSMiddleware, allow_origins=settings.CORS_ORIGINS, allow_methods=['*'], allow_headers=['*'])


def _page(items: list, page: int, page_size: int) -> list:
    return items[(page - 1) * page_size: page * page_size]


def _job_or_404(job_id: str, kind: str) -> dict:
    job = store.get(job_id)
    if job is None or job['kind'] != kind:
        raise HTTPException(404, {'simulation': 'Simulation not found', 'assessment': 'Assessment not found',
                                  'eqm': 'EQ run not found'}[kind])
    return job


def _cancel(job: dict) -> dict:
    if job['status'] not in ('queued', 'running'):
        raise HTTPException(409, detail={'code': 'not-active', 'message': f'The job is already {job["status"]}.'})
    return public(store.cancel(job['_id']), store)


def _list(kind: str, status: str | None, page: int, page_size: int, keep=lambda j: True) -> dict:
    jobs = [j for j in store.all(kind) if (status is None or j['status'] == status) and keep(j)]
    return {'items': [public(j, store) for j in _page(jobs, page, page_size)], 'total': len(jobs)}


# scenario and KPIs -------------------------------------------------------------------------------------------

@app.get('/api/health')
def health():
    return {'status': 'ok'}


def _outputs_state(scenario: str) -> dict:
    files = SIMULATION_OUTPUTS[scenario]
    present = [f for f in files if os.path.isfile(f)]
    updated = max((os.path.getmtime(f) for f in present), default=None)
    return {'available': all(os.path.isfile(f) for f in files[:3]), 'hourly': os.path.isfile(files[3]),
            'updated_at': None if updated is None else
            datetime.fromtimestamp(updated, timezone.utc).isoformat(timespec='seconds')}


@app.get('/api/scenario')
def scenario():
    """The LuST scenario as the assessment and the EQ runs use it, and whether its simulation outputs exist."""
    return {'_id': 'lust', 'name': 'LuST (Luxembourg)',
            'network': os.path.relpath(cfg.DISASTER_NET, cfg.REPO_ROOT), 'edges': len(base_capacities()),
            'horizon': [cfg.SIM_BEGIN, cfg.SIM_END], 'closure_window': list(cfg.CLOSURE_WINDOW),
            'closed_edges': closed_edges(), 'stress_window': cfg.STRESS_WINDOW, 'entropy_window': cfg.ENTROPY_WINDOW,
            'hourly_period': cfg.HOURLY_PERIOD,
            'outputs': {s: _outputs_state(s) for s in SIMULATION_OUTPUTS}}


@app.get('/api/impact-classes')
def impact_classes():
    """D3.2 Table 9. A score belongs to the first class whose upper bound it does not exceed (see kpi_functions)."""
    bounds = [(0.0, 0, 'No performance loss'), (0.05, 1, 'Negligible')] + IMPACT_CLASSES
    items, lower = [], None
    for upper, cls, label in bounds:
        items.append({'_id': str(cls), 'class': cls, 'name': label, 'lower': lower,
                      'upper': None if math.isinf(upper) else upper})
        lower = upper
    return {'items': items, 'total': len(items)}


class Kpis(BaseModel):
    """Core KPIs in [0, 1]. Q (equity) and I (intermodal synergy) are optional: with both, the full Tier-2 index
    (D3.2 Eq. 6) is used, without them the limited index (M + R + C - S + 1) / 4."""
    M: float = Field(ge=0, le=1)
    R: float = Field(ge=0, le=1)
    C: float = Field(ge=0, le=1)
    S: float = Field(ge=0, le=1)
    Q: float | None = Field(None, ge=0, le=1)
    I: float | None = Field(None, ge=0, le=1)


class VulnerabilityRequest(BaseModel):
    baseline: Kpis
    post: Kpis


@app.post('/api/vulnerability')
def vulnerability(req: VulnerabilityRequest):
    """E for both states, ΔE = 1 - E_post / E_base (not clamped) and the impact class."""
    full = [k.Q is not None and k.I is not None for k in (req.baseline, req.post)]
    partial = [(k.Q is None) != (k.I is None) for k in (req.baseline, req.post)]
    if any(partial) or full[0] != full[1]:
        raise HTTPException(422, detail={'code': 'kpi-set', 'message':
                                         'Give Q and I for both states (full index) or for neither (limited index).'})
    index = performance_index if full[0] else performance_index_limited
    E_base, E_post = index(req.baseline.model_dump()), index(req.post.model_dump())
    delta = system_vulnerability(E_base, E_post)
    cls, label = classify_vulnerability(delta)
    return {'index': 'tier2' if full[0] else 'limited', 'E_base': E_base, 'E_post': E_post, 'delta_E': delta,
            'impact_class': cls, 'impact_label': label}


# simulations -------------------------------------------------------------------------------------------------

@app.post('/api/simulations', status_code=201)
def create_simulation(spec: SimulationCreate):
    name = spec.name or {'baseline': 'Baseline', 'disaster': 'Post-disaster'}[spec.scenario]
    job = store.create('simulation', name, spec.model_dump())
    store.tick()  # start now if a slot is free
    return public(store.get(job['_id']), store)


@app.get('/api/simulations')
def list_simulations(scenario: Literal['baseline', 'disaster'] | None = None, status: Status | None = None,
                     page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    return _list('simulation', status, page, page_size, lambda j: scenario is None or j['spec']['scenario'] == scenario)


@app.get('/api/simulations/{job_id}')
def get_simulation(job_id: str):
    return public(_job_or_404(job_id, 'simulation'), store)


@app.get('/api/simulations/{job_id}/log')
def get_simulation_log(job_id: str, lines: int = Query(200, ge=1, le=2000)):
    _job_or_404(job_id, 'simulation')
    return {'lines': store.log_tail(job_id, lines)}


@app.post('/api/simulations/{job_id}/cancel')
def cancel_simulation(job_id: str):
    return _cancel(_job_or_404(job_id, 'simulation'))


# static assessments ------------------------------------------------------------------------------------------

@app.post('/api/assessments', status_code=201)
def create_assessment(spec: AssessmentCreate):
    job = store.create('assessment', spec.name or 'Static assessment', spec.model_dump())
    store.tick()
    return public(store.get(job['_id']), store)


@app.get('/api/assessments')
def list_assessments(status: Status | None = None, page: int = Query(1, ge=1),
                     page_size: int = Query(20, ge=1, le=100)):
    out = _list('assessment', status, page, page_size)
    for item in out['items']:
        item['result'] = store.assessment_result(item['_id'])
    return out


@app.get('/api/assessments/{job_id}')
def get_assessment(job_id: str):
    job = _job_or_404(job_id, 'assessment')
    return public(job, store) | {'result': store.assessment_result(job_id)}


@app.get('/api/assessments/{job_id}/log')
def get_assessment_log(job_id: str, lines: int = Query(200, ge=1, le=2000)):
    _job_or_404(job_id, 'assessment')
    return {'lines': store.log_tail(job_id, lines)}


@app.post('/api/assessments/{job_id}/cancel')
def cancel_assessment(job_id: str):
    return _cancel(_job_or_404(job_id, 'assessment'))


# EQ learning runs --------------------------------------------------------------------------------------------

@app.post('/api/eqm-runs', status_code=201)
def create_eqm_run(spec: EqmRunCreate):
    job = store.create('eqm', spec.name, spec.model_dump())
    store.tick()
    return public(store.get(job['_id']), store)


def _eqm_summary(job_id: str) -> dict:
    record = store.eqm_record(job_id) or {}
    static = record.get('static')
    return {'static': static, 'final': record.get('final'),
            'E_post': static['E_post'] if static else None}


@app.get('/api/eqm-runs')
def list_eqm_runs(status: Status | None = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    out = _list('eqm', status, page, page_size)
    for item in out['items']:
        item |= _eqm_summary(item['_id'])
    return out


@app.get('/api/eqm-runs/{job_id}')
def get_eqm_run(job_id: str):
    """The run with its static assessment, the per-day history so far and, when finished, the final result."""
    job = _job_or_404(job_id, 'eqm')
    record = store.eqm_record(job_id) or {}
    return public(job, store) | _eqm_summary(job_id) | {
        'history': record.get('history', []), 'day_seconds': record.get('day_seconds', []),
        'provenance': record.get('provenance')}


@app.get('/api/eqm-runs/{job_id}/capacities')
def get_eqm_capacities(job_id: str, changed_only: bool = False, min_change: float = Query(0.0, ge=0),
                       page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=10000)):
    """Learned capacities per edge, largest relative change first. `factor` = learned / base."""
    _job_or_404(job_id, 'eqm')
    learned = store.eqm_capacities(job_id)
    if learned is None:
        raise HTTPException(409, detail={'code': 'no-capacities',
                                         'message': 'The learned capacities are written when the run finishes.'})
    base = base_capacities()
    rows = [{'_id': e, 'base': base.get(e), 'learned': c, 'factor': c / base[e] if base.get(e) else None}
            for e, c in learned.items()]
    if changed_only or min_change:
        rows = [r for r in rows if r['factor'] is not None and abs(r['factor'] - 1) > max(min_change, 1e-9)]
    rows.sort(key=lambda r: -abs((r['factor'] or 1) - 1))
    return {'items': _page(rows, page, page_size), 'total': len(rows)}


@app.get('/api/eqm-runs/{job_id}/log')
def get_eqm_log(job_id: str, lines: int = Query(200, ge=1, le=2000)):
    _job_or_404(job_id, 'eqm')
    return {'lines': store.log_tail(job_id, lines)}


@app.post('/api/eqm-runs/{job_id}/cancel')
def cancel_eqm_run(job_id: str):
    return _cancel(_job_or_404(job_id, 'eqm'))
