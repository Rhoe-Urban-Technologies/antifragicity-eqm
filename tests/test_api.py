import json
import shutil

import pytest
from fastapi.testclient import TestClient

import scenario_config as cfg
from eqm_api import settings
from eqm_api.main import app, base_capacities, store


@pytest.fixture(scope='module')
def client():
    with TestClient(app) as c:
        yield c
    shutil.rmtree(settings.JOBS_DIR, ignore_errors=True)


def _running(job_id, **fields):
    """Mark a job as running (no process: MAX_RUNNING_JOBS is 0 in tests)."""
    job = store.get(job_id)
    job.update(status='running', started_at='2026-01-01T00:00:00+00:00', pid=None, **fields)
    store.save(job)


# scenario and KPIs -------------------------------------------------------------------------------------------

def test_scenario(client):
    s = client.get('/api/scenario').json()
    assert s['edges'] == len(base_capacities()) > 1000
    assert s['horizon'] == [0, 90000] and s['closed_edges']
    assert set(s['outputs']) == {'baseline', 'disaster'}


def test_impact_classes(client):
    r = client.get('/api/impact-classes').json()
    assert r['total'] == 6 and [i['class'] for i in r['items']] == [0, 1, 2, 3, 4, 5]
    moderate = r['items'][3]
    assert (moderate['name'], moderate['lower'], moderate['upper']) == ('Moderate', 0.15, 0.30)
    assert r['items'][-1]['upper'] is None


def test_vulnerability_bratislava(client):
    r = client.post('/api/vulnerability', json={
        'baseline': {'M': 0.82, 'R': 0.70, 'C': 0.75, 'S': 0.35, 'Q': 0.25, 'I': 0.68},
        'post': {'M': 0.68, 'R': 0.45, 'C': 0.62, 'S': 0.72, 'Q': 0.48, 'I': 0.55}}).json()
    assert r['index'] == 'tier2' and r['delta_E'] == pytest.approx(0.2874, abs=1e-4)
    assert (r['impact_class'], r['impact_label']) == (3, 'Moderate')


def test_vulnerability_limited_and_errors(client):
    kpis = {'M': 1, 'R': 1, 'C': 1, 'S': 0}
    r = client.post('/api/vulnerability', json={'baseline': kpis, 'post': kpis}).json()
    assert r['index'] == 'limited' and r['E_base'] == 1 and r['impact_class'] == 0
    mixed = client.post('/api/vulnerability', json={'baseline': kpis | {'Q': 0.1, 'I': 0.1}, 'post': kpis})
    assert mixed.status_code == 422 and mixed.json()['detail']['code'] == 'kpi-set'
    assert client.post('/api/vulnerability', json={'baseline': kpis | {'M': 1.5}, 'post': kpis}).status_code == 422


# jobs --------------------------------------------------------------------------------------------------------

def test_simulation_commands(client, monkeypatch):
    monkeypatch.setenv('SUMO_BINARY', '/opt/sumo/bin/sumo')
    baseline = client.post('/api/simulations', json={'scenario': 'baseline'})
    assert baseline.status_code == 201 and baseline.json()['status'] == 'queued'
    cmd, cwd = store.command(store.get(baseline.json()['_id']))
    assert cmd[-1] == 'get_benchmark.py' and cwd == settings.BENCHMARK_DIR
    disaster = client.post('/api/simulations', json={'scenario': 'disaster'}).json()
    cmd, _ = store.command(store.get(disaster['_id']))
    assert cmd[:2] == ['/opt/sumo/bin/sumo', '-c'] and cmd[2].endswith('disaster.sumocfg')
    assert client.get('/api/simulations', params={'scenario': 'disaster'}).json()['total'] == 1
    assert client.post('/api/simulations', json={'scenario': 'flood'}).status_code == 422


def test_simulation_progress_from_step_log(client):
    job = client.post('/api/simulations', json={'scenario': 'disaster'}).json()
    _running(job['_id'])
    (store.folder(job['_id']) / 'job.log').write_text('Loading...\nStep #44900.00 (1ms)\rStep #45000.00 (1ms)\n')
    progress = client.get(f'/api/simulations/{job["_id"]}').json()['progress']
    assert progress['simulated_seconds'] == 45000 and progress['fraction'] == pytest.approx(0.5)
    store.cancel(job['_id'])


def test_assessment_command(client):
    r = client.post('/api/assessments', json={'stress_window': None})
    assert r.status_code == 201
    cmd, _ = store.command(store.get(r.json()['_id']))
    assert cmd[cmd.index('--stress-window') + 1] == 'none'
    assert cmd[cmd.index('--output') + 1].endswith('result.json')
    assert client.post('/api/assessments', json={'stress_window': [61200, 63000]}).status_code == 422


def test_create_and_cancel_eqm_run(client):
    r = client.post('/api/eqm-runs', json={'name': 'Short', 'settings': {'max_iterations': 3,
                                                                          'stress_window': [61200, 64800]}})
    assert r.status_code == 201, r.text
    job = r.json()
    assert job['status'] == 'queued' and job['progress']['simulations_total'] == 4
    written = json.loads((store.folder(job['_id']) / 'settings.json').read_text())
    assert written['max_iterations'] == 3 and written['learning_rate'] == 0.1
    cmd, _ = store.command(store.get(job['_id']))
    assert '--settings' in cmd and cmd[cmd.index('--output') + 1].endswith('run')
    assert client.post(f'/api/eqm-runs/{job["_id"]}/cancel').json()['status'] == 'cancelled'
    assert client.post(f'/api/eqm-runs/{job["_id"]}/cancel').status_code == 409


@pytest.mark.parametrize('bad', [
    {'stress_window': [61200, 63000]},              # not whole hours
    {'begin_time': 1000, 'end_time': 500},          # empty horizon
    {'capacity_min_factor': 1.5},                   # must be ≤ 1
])
def test_eqm_settings_are_checked(client, bad):
    assert client.post('/api/eqm-runs', json={'name': 'Bad', 'settings': bad}).status_code == 422


def test_eqm_progress_result_and_capacities(client):
    job = client.post('/api/eqm-runs', json={'name': 'Fake', 'settings': {'max_iterations': 2}}).json()
    _running(job['_id'])
    run = store.folder(job['_id']) / 'run'
    run.mkdir(exist_ok=True)
    day = {'day': 1, 'E': 0.8876, 'M': 0.99, 'R': 0.69, 'C': 0.87, 'S': 0.01}
    record = {'status': 'running', 'static': {'E_base': 0.8886, 'E_post': 0.8876}, 'history': [day],
              'day_seconds': [1200.0]}
    (run / 'run.json').write_text(json.dumps(record))
    (store.folder(job['_id']) / 'job.log').write_text('Step #45000.00 (1ms)\n')

    got = client.get(f'/api/eqm-runs/{job["_id"]}').json()
    p = got['progress']
    assert (p['stage'], p['simulations_done'], p['simulations_total'], p['days_done']) == ('learning', 1, 3, 1)
    assert p['current'] == {'simulation': 2, 'fraction': pytest.approx(0.5)} and p['eta_seconds'] == 2400
    assert got['history'] == [day] and got['E_post'] == 0.8876
    assert client.get(f'/api/eqm-runs/{job["_id"]}/capacities').status_code == 409

    # The script finishes: capacities.json, then run.json with status "finished".
    base = base_capacities()
    edges = list(base)[:3]
    (run / 'capacities.json').write_text(json.dumps({edges[0]: base[edges[0]] * 1.2, edges[1]: base[edges[1]],
                                                     edges[2]: base[edges[2]] * 0.9}))
    record.update(status='finished', final={'E': 0.8875, 'delta_E': 0.0013, 'impact_class': 1})
    (run / 'run.json').write_text(json.dumps(record))
    store.tick()  # the process (none here) has ended: settle the job from its output
    got = client.get(f'/api/eqm-runs/{job["_id"]}').json()
    assert got['status'] == 'completed' and got['final']['impact_class'] == 1

    caps = client.get(f'/api/eqm-runs/{job["_id"]}/capacities', params={'changed_only': True}).json()
    assert caps['total'] == 2 and caps['items'][0]['_id'] == edges[0]
    assert caps['items'][0]['factor'] == pytest.approx(1.2)


def test_failed_job_reports_the_log(client):
    job = client.post('/api/assessments', json={}).json()
    _running(job['_id'])
    (store.folder(job['_id']) / 'job.log').write_text('Missing simulation outputs:\n  baseline/x.xml\n')
    store.tick()
    got = client.get(f'/api/assessments/{job["_id"]}').json()
    assert got['status'] == 'failed' and got['error'].startswith('Missing simulation outputs')


def test_unknown_or_wrong_kind_is_404(client):
    sim = client.post('/api/simulations', json={'scenario': 'baseline'}).json()
    assert client.get(f'/api/eqm-runs/{sim["_id"]}').status_code == 404
    assert client.get('/api/assessments/nope').status_code == 404
