# Antifragicity Equilibrium Model

This repository contains code to replicate and extend the quantitative vulnerability
assessment described in deliverable **D3.2**.

| File | Purpose |
|---|---|
| `kpi_functions.py` | KPIs $M, R, C, S$, performance indices, $\Delta E$, impact classes |
| `eq_model.py` | Iterative equilibrium (learning) loop over SUMO runs (§4) |
| `scenario_config.py` | Paths, horizon and KPI windows for the LuST scenario |
| `sumo_runner.py` | Finds the SUMO binary, writes per-run configs, runs SUMO |
| `luxembourg_example.py` | Static baseline vs. post‑disaster assessment (§3) |
| `luxembourg_example_eq_model.py` | Static assessment + learning loop, with a run record |
| `bratislava_flood_example.py` | D3.2 worked example (§2) |
| `demo_eq_model.py` | Learning loop on a synthetic 4‑edge network |
| `parallel_simulations.py` | Runs several SUMO simulations at once (work in progress) |
| `eqm_api/` | FastAPI back end: KPIs, vulnerability, simulations and EQ runs as local jobs (§5) |
| `tests/` | `pytest` suite (no SUMO needed) |
| `AntifragiCity-WP4/` | Submodule with the LuST scenario and WP4 scripts |

## 1. KPI functions

The module `kpi_functions.py` computes four core network performance indicators
directly from SUMO simulation results. All indicators are normalised to $[0,1]$.

### 1.1 Mobility Throughput ($M$)

**Formula**  

$$ M = \frac{N_{\text{arrived}}}{N_{\text{loaded}}} $$

where  

- $N_{\text{arrived}}$ = number of trips with a valid `arrival` time,  
- $N_{\text{loaded}}$  = final `loaded` count in SUMO `summary.xml`.

**SUMO data sources** – `tripinfo.xml` and `summary.xml`  
The function `compute_throughput(tripinfo_file, summary_file)` counts arrived
trips from `tripinfo.xml` and divides by the full loaded demand from
`summary.xml`. This avoids overestimating throughput when vehicles are discarded
before departure because of `max-depart-delay`.

**Important SUMO setting**  
If SUMO teleports stalled vehicles forward along their routes, they can still
receive an `arrival` attribute and artificially inflate $M$. The scenario
configs in this repository therefore use:

- `time-to-teleport="600"` together with `time-to-teleport.remove="true"`
- `collision.action="remove"`
- `tripinfo-output.write-unfinished="true"`

This means jammed or colliding vehicles are removed instead of being counted as
successful arrivals, and vehicles still in the network at the horizon are kept
visible in the tripinfo output.

---
### 1.2 Redundancy ($R$)

**Formula**  

For a random sample of $K$ origin‑destination pairs $(o,d)$:

$$ k_{o,d} = \text{number of edge‑disjoint paths from } o \text{ to } d $$

$$ R = \min\!\left(1,\; \frac{\mathrm{avg}(k)}{\mathrm{avg\ out\ degree}}\right) $$

The average out‑degree of the network serves as an upper bound on the possible
number of disjoint paths, normalising the result to $[0,1]$.

**SUMO data source** – the network file `.net.xml` (via `sumolib`)  
The function `compute_redundancy(net_file)` builds a directed graph with
`networkx`, samples OD pairs, and counts edge‑disjoint paths using
`nx.edge_disjoint_paths`. The graph is constructed from all nodes and edges in
the SUMO network; edge attributes (length, free‑flow speed) are not used for
this metric.

OD pairs are sampled with a fixed seed (`seed=42` by default, over the sorted
node list), so $R$ is reproducible and the baseline and post‑disaster networks
are evaluated on the same OD sample. Pass `seed=None` for random sampling.

---
### 1.3 Flow Entropy ($C$)

**Formula** (D3.2 Annex B, Indicator R2, Eq. 17)

Let $f_i$ be the number of vehicles that entered link $i$, and $N$ the total
number of (non‑internal) links in the network, including links without flow.

$$ p_i = \frac{f_i}{\sum_j f_j} \qquad\qquad
   C = \frac{-\sum_i p_i \log p_i}{\log N} $$

$C = 0$ indicates all flow is on a single link; $C = 1$ indicates flow spread
evenly over all links. When links are closed, flow concentrates on the
remaining links and $C$ drops, so this KPI reacts to disruptions.

**SUMO data sources** – `edgedata.xml` (attribute `entered`) and the network
file (for $N$)  
`compute_link_entropy(edgedata_file, net_file, window=None)` is the default $C$
used by the examples and the learning loop. With `window=(begin_s, end_s)` it
uses only that part of the day (this needs hourly edgedata, see §1.4).

**OD-based variants** – `compute_entropy(tripinfo_file, route_files=None)`
computes the entropy of the origin–destination distribution instead (D3.2 §4
describes $C$ this way, while Annex B gives the link-flow formula above):

- default (tripinfo-only): OD flows from executed trips,
- demand mode (`route_files` given): OD flows from planned demand, so trips
  discarded before departure are included. This is identical for every
  scenario that loads the same route files, so it cannot show a disruption.

### 1.4 Stress Index ($S$)

**Formula (per edge $a$)**  

$$ \text{stress}_a = \min\!\left(
   \frac{f_a}{C_a} \cdot \left(1 - \frac{v_a}{v_{0,a}}\right),\;
   1.0
\right) $$

where  

- $f_a$   = flow on edge $a$ (veh/h): vehicles entered during the measurement
  period ÷ the period length,  
- $C_a$   = capacity of edge $a$ (number of lanes × 1800 veh/h, or a supplied
  per-edge capacity),  
- $v_a$   = mean speed on edge $a$ during the period (m/s),  
- $v_{0,a}$ = free‑flow speed of edge $a$ (m/s).

The overall stress index is the unweighted average over all non‑internal edges:

$$ S = \frac{1}{|A|} \sum_{a \in A} \text{stress}_a $$

**Measurement window** – averaged over a whole 25 h day, flows are far below
capacity and $S \approx 0$. The examples therefore measure $S$ over a fixed
window, by default the closure hour 17:00–18:00 (`STRESS_WINDOW` in
`scenario_config.py`), using the same window for baseline and post‑disaster
runs. `compute_stress(net_file, edgedata_file, window=(61200, 64800))` needs
edgedata aggregated hourly. The scenario configs write it via
`edgedata.hourly.baseline.add.xml` / `edgedata.hourly.disaster.add.xml`
(in the submodule fork, see §3.3.2), and
the learning loop writes its own. Without `window`, the period length is taken
from the edgedata intervals, so it always matches the simulated horizon.

**SUMO data sources**  

- **`edgedata.xml`** – flow (attribute `entered` or `left`) and, if available,
  speed (attribute `speed`). With several intervals, flows are summed and
  speeds are averaged weighted by `sampledSeconds`. If speed is missing, the
  function falls back to the free‑flow speed (yielding zero stress).  
- **`.net.xml`** – lane count and free‑flow speed (`speed` attribute of the
  first lane of each edge).  

`compute_edge_stresses(...)` returns the per-edge values and `compute_stress(...)`
their mean. All options after the two file paths are keyword‑only:
`capacity_per_lane`, `sim_duration_hours`, `window` and `capacities` (a
`{edge_id: veh/h}` override, used for the learned capacities in §4).

### 1.5 Performance index

$$ E_{\text{Tier2}} = \frac{M + R + C + I - S - Q + 2}{6} $$

This is the official index defined in D3.2. It uses the two additional KPIs
$Q$ (equity) and $I$ (intermodal synergy), which are **not implemented** in
this code base.

### 1.6 System vulnerability ($\Delta E$)

$$ \Delta E = 1 - \frac{E_{\text{post}}}{E_{\text{base}}} \qquad (E_{\text{base}} > 0) $$

This formula is applied identically regardless of which performance index is used
(full or limited). The function `system_vulnerability(E_base, E_post)` returns a
single number; $\Delta E \le 0$ indicates no loss (or an improvement).
D3.2 (Eq. 10) defines $\Delta E_s = \max(0,\, 1 - E_{\text{post}}/E_{\text{base}})$;
the function deliberately does **not** clamp, so improvements (e.g. after the
learning loop in §4) stay visible. Clamp when reporting against D3.2.

### 1.7 Impact classification

`classify_vulnerability(delta)` maps $\Delta E_s$ (or a group-level
$\Delta Q_s$) to the D3.2 impact scale (Table 9, Eq. 20) and returns
`(class, label)`:

| Class | Label | $\Delta E_s$ |
|---|---|---|
| 0 | No performance loss | $\le 0$ |
| 1 | Negligible | $< 0.05$ |
| 2 | Minor | $0.05$–$0.15$ |
| 3 | Moderate | $(0.15,\ 0.30]$ |
| 4 | Major | $(0.30,\ 0.50]$ |
| 5 | Catastrophic | $> 0.50$ |

Values on a boundary go to the lower class, following the right-closed
intervals of D3.2 Eq. 10 (e.g. $(0.15, 0.30]$ = Moderate). The exception is
0.05, which Table 9 puts in Minor. The D3.2 equity escalation rule
($r_{\text{equity}} > 1.5$ raises the class by one, Eq. 27) is not applied,
because $r_{\text{equity}}$ is not computed.

## 2. Bratislava Flood Example (`bratislava_flood_example.py`)

This script **exactly reproduces** the vulnerability calculation from the
Bratislava flood worked example (D3.2, Section 7.1). It uses the **synthetic KPI
values** provided in the deliverable to compute:

- Baseline and post‑disaster Tier 2 performance indices $E_{\text{base}}, E_{\text{post}}$
- System vulnerability $\Delta E = 1 - E_{\text{post}} / E_{\text{base}}$
- Equity vulnerability $\Delta Q = (Q_{\text{post}} - Q_{\text{base}}) / (1 - Q_{\text{base}})$
- All six core KPIs ($M$, $R$, $C$, $S$, $Q$, $I$) are hard‑coded from the example.

The script requires no external data and serves as a **reference implementation**
of the D3.2 vulnerability formulas.

**Output** (the $E$, $\Delta E$ and $\Delta Q$ values match the deliverable):

```commandline
Performance index E_base = 0.7250
Performance index E_post = 0.5167
System vulnerability ΔE_s = 1 - 0.5167/0.7250 = 0.2874
  → (0.29 : Class 3 – Moderate vulnerability, D3.2 Table 9)
Equity vulnerability ΔQ_s = (0.48 - 0.25) / (1 - 0.25) = 0.3067
  → (0.31 : Class 4 – Major equity vulnerability for elderly)
```

The labels come from `classify_vulnerability` (see §1.7), which implements the
D3.2 impact scale. In the deliverable's worked example (Step 3.5) the text calls
$\Delta E = 0.287$ "Moderate to Major (threshold 0.30)", but 0.287 is below that
threshold and falls in the Moderate band of Table 9 (0.15–0.30), so it is
reported as Moderate here. $\Delta Q = 0.31$ is Major in both.

## 3. Luxembourg LuST Scenario Vulnerability Assessment

We applied the same vulnerability methodology to the **LuST scenario**
(Luxembourg SUMO Traffic) by comparing a **baseline** simulation with a
**post‑disaster** simulation in which six critical edges are closed during the
evening peak (17:00–18:00, i.e. 61200–64800 s) via a SUMO rerouter
(`disaster_closures.add.xml`, loaded by `disaster.sumocfg`). The post‑disaster
run uses the same base network `lust.net.xml` as the baseline; the edges are
only closed by the rerouter during that interval.

### 3.1 Simplified performance index

The official Tier 2 vulnerability index requires six core KPIs:

$$E_{\text{Tier2}} = \frac{M + R + C + I - S - Q + 2}{6}$$

Two of these, **equity ($Q$)** and **intermodal synergy ($I$)**, cannot be
computed from standard SUMO outputs alone:

- **$Q$ (Theil‑T equity index)** requires demographic data (census tracts,
  population groups) and accessibility measures that are external to the traffic
  simulation.
- **$I$ (intermodal synergy)** requires detailed coordinates of transit stops
  and transfer walking distances; these are present in the network but require a
  separate calculation that has not yet been implemented.

Because $Q$ and $I$ are missing, using the original formula would produce
meaningless results (or require placeholder values that distort the outcome).
For the present assessment we therefore adopted a **limited 4‑KPI performance
index** that uses only the indicators that can be derived directly from SUMO
outputs:

$$E_{\text{limited}} = \frac{M + R + C - S + 1}{4}$$

The formula preserves the structural principle of rewarding higher throughput,
redundancy, and flow diversity while penalising stress, and it maps the result
to $[0,1]$ under the same assumption that each KPI lies in $[0,1]$. It was
constructed using the same normalisation logic as the original Tier 2 index
(i.e., shift the numerator so the minimum possible value is 0, then divide by
the range). Once $Q$ and $I$ become available, the full Tier 2 index will
replace this temporary version.

The limited index is used **consistently** for both baseline and post‑disaster
states when computing the system vulnerability:

$$\Delta E = 1 - \frac{E_{\text{post}}}{E_{\text{base}}}$$

### 3.2 Vehicle routing strategy in LuST

#### What are DUA and DUE?

The LuST scenario provides routes generated by two different traffic equilibrium
concepts, each reflecting different assumptions about driver behavior and network
knowledge:

**DUA — Dynamic User Assignment**

In DUA, each vehicle is assigned a route that minimises travel cost (typically
travel time) **based on an empty network**. The assumption is that drivers have
perfect information about the uncongested network but do not anticipate congestion
created by other vehicles. SUMO's `duarouter` tool computes these optimal routes.

- Generated on: uncongested network (no existing traffic)
- Rationale: user-optimal paths; each driver independently picks the best route
- Realism: not realistic on its own, because it ignores interaction with other vehicles
- Typical use: **combined with runtime rerouting during simulation** to let drivers
  adapt as congestion emerges (this is what this project does)

**DUE — Dynamic User Equilibrium**

In DUE, the traffic flow reaches a state where, for each origin-destination pair
at each instant of time, all drivers departing at the same time experience equal
and minimal travel times. This means no driver can unilaterally reduce their travel
cost by switching to a different path. DUE is formulated as the dynamic extension
of Wardrop's user equilibrium principle.

SUMO's `duaIterate` tool iteratively computes DUE by:

1. Running a simulation with current routes
2. Computing new travel times based on observed congestion
3. Recomputing routes (DUA) using those new travel times
4. Randomly selecting among the best routes (to encourage exploration)
5. Repeating until convergence (routes and flows stabilise)

- Generated over: many simulation iterations reflecting congestion feedback
- Rationale: equilibrium state where drivers cannot improve by unilateral reroutes
- Realism: **best case** — approximates realistic long-term driver behavior under
  consistent demand
- Typical use: benchmark or upper-bound performance

#### What this project uses

In this repository, the main KPI and vulnerability workflow is run with
`scenario/dua.static.sumocfg`, which loads **DUA-based traces** (`DUARoutes/*.rou.xml`)
with runtime rerouting enabled (`device.rerouting.probability="0.70"`). This means:

- Initial routes are user-optimal on an uncongested network (DUA)
- Drivers **dynamically reroute every 5 minutes** (300 seconds) during the simulation
- Rerouting decisions are based on **observed travel times** from the previous interval
- This creates a **realistic adaptive behavior** without the computational cost of full DUE iteration

The `due.*.sumocfg` configurations are available in
the scenario folder for DUE-based experiments, but they are not the default path
used by the current examples.

### 3.3 Steps to reproduce

#### 3.3.1 Install requirements

- Python 3.10+ (the code uses `X | None` type hints)
- SUMO ≥ 1.20. The scripts find the `sumo` binary via `$SUMO_BINARY`,
  `$SUMO_HOME/bin`, `PATH`, or the default macOS EclipseSUMO location (see
  `sumo_runner.find_sumo_binary`).
- Python packages:

```bash
pip install -r requirements.txt        # sumolib, networkx, tqdm (+ matplotlib for the submodule plots)
pip install -r requirements-dev.txt    # adds pytest
```

#### 3.3.2 Get the scenario submodule

`AntifragiCity-WP4` is a submodule pointing to the fork
[`Rhoe-Urban-Technologies/AntifragiCity-WP4`](https://github.com/Rhoe-Urban-Technologies/AntifragiCity-WP4),
branch `antifragicity-eqm`. The fork carries this project's scenario changes on
top of the upstream repository (`sunlinghang/AntifragiCity-WP4`):

- `disaster.sumocfg` runs for 25 h (`<end value="90000"/>`), like the baseline;
- `edgedata.hourly.baseline.add.xml` / `edgedata.hourly.disaster.add.xml`
  write hourly edgedata, and are listed in `additional-files` of
  `dua.static.sumocfg` / `disaster.sumocfg`;
- the earlier commit disabling teleports, removing vehicles after collisions and
  writing unfinished trips.

```bash
git clone --recurse-submodules git@github.com:Rhoe-Urban-Technologies/AntifragiCity.git
# or, in an existing clone:
git submodule sync && git submodule update --init
```

To pick up new upstream work (in `AntifragiCity-WP4/`):

```bash
git remote add upstream https://github.com/sunlinghang/AntifragiCity-WP4.git   # once
git fetch upstream
git switch antifragicity-eqm
git merge upstream/main
git push origin antifragicity-eqm
```

Then commit the new submodule pointer in this repository
(`git add AntifragiCity-WP4`).

The learning loop (`eq_model.py`) does not depend on these config changes: it
writes its own hourly edgedata additional and passes `--end` on the command line.

Not done yet: `device.rerouting.probability` is 0.70 in `dua.static.sumocfg`
and 1.00 in `disaster.sumocfg`; set both to the same value before comparing
runs.

#### 3.3.3 Run the baseline and disaster simulations

Follow `AntifragiCity-WP4/readme.md`. From `AntifragiCity-WP4/T4.1-KPIs/`:

```bash
python get_benchmark.py                                  # baseline → scenario/baseline/
sumo -c LuSTScenario-master/scenario/disaster.sumocfg    # post-disaster → scenario/outputs/
```

With the changes from §3.3.2, both runs last 25 h (0–90000 s) and also write
hourly edgedata (`*.hourly.edgedata.xml`), which the windowed stress index
needs. Outputs
produced before these config changes have no hourly file and must be
regenerated.

To get a progress bar (tqdm) instead of SUMO's raw step log, run a config
through `sumo_runner.py` from the repository root; extra SUMO options are
passed through:

```bash
python sumo_runner.py AntifragiCity-WP4/T4.1-KPIs/LuSTScenario-master/scenario/disaster.sumocfg
```

The learning loop (`luxembourg_example_eq_model.py`) shows one bar per
simulated day ("Day 3/30", then "Final run"). The bar needs `tqdm` in the
interpreter you run with (use `.venv`); it appears in terminals and in
PyCharm's run console, and `SUMO_PROGRESS=1` / `0` forces it on or off.

#### 3.3.4 Run `luxembourg_example.py` to compute KPIs and vulnerability

```bash
python luxembourg_example.py
python luxembourg_example.py --stress-window none --output result.json   # whole-day S, result as JSON
```

All paths and the KPI windows are defined in `scenario_config.py`. Windows are
`BEGIN,END` in seconds (whole hours, since they read hourly edgedata) or `none`.

#### 3.3.5 Run the tests

```bash
python -m pytest
```

The tests use a small synthetic network (`tests/data/small.net.xml`) and do
not need SUMO.

## 4. Iterative Equilibrium Model

Beyond the static vulnerability comparison, the **Three‑Layer Equilibrium Model**
(D2.3, Algorithm 1) introduces a learning loop that allows the network to
**adapt its capacities over multiple “days”** in order to improve the performance
index after a disruption. This is implemented in `eq_model.py`.

### 4.1 How the learning loop works

The loop starts from the **post‑disaster** capacities (the same base network,
with closures applied via the rerouter). Redundancy $R$ is computed once,
because capacity updates do not change the network topology. Each iteration
(one “day”) performs:

1. **Apply current capacities** – writes a modified SUMO network where lane
   speeds are scaled proportionally to the capacity ratio (a proxy for changing
   throughput). The speed factor is capped at `max_speed_factor` (default 1.3)
   so speed limits stay plausible, while the learned capacity itself can range
   over $[0.5, 2.0] \times$ base.
2. **Run SUMO simulation** – runs `disaster.sumocfg` with the modified network
   swapped in, so it uses the same route files (including buses/transit), bus
   stops and rerouter closures as the static disaster run. The simulated
   horizon (`begin_time`/`end_time`, default 0–90000 s, i.e. 25 h like the
   baseline) is passed to SUMO. The loop also writes its own hourly edgedata
   for windowed KPIs.
3. **Compute core KPIs** – $M$ from `tripinfo.xml`/`summary.xml`, link entropy
   $C$ and per‑edge stress from edgedata. Stress uses the **original** network
   for free‑flow speeds and lane counts and the **learned** capacities for
   $C_a$. Otherwise raising a capacity (which raises the simulated speed limit)
   would raise the measured speed drop and make stress go *up*. $S$ is the
   mean of the per‑edge stresses; `stress_window` / `entropy_window` restrict
   $S$ / $C$ to a time window (the example uses the closure hour for $S$).
4. **Update SRI components** – operational elasticity and infrastructure pressure.
5. **Calculate a short‑term capacity gradient** $g_{\text{short}}$ from the
   per‑edge stresses (§4.3).
6. **Update capacities** – multiply each edge’s capacity by $(1 + g_{\text{short}})$,
   then clip to $[0.5 \times \text{base}, 2.0 \times \text{base}]$.
7. **Check convergence** – stop when the performance index $E$ stabilises for
   five consecutive days.

A final simulation with the learned capacities gives the reported $E$. The
final configuration is the **antifragile equilibrium** for the given disruption.

### 4.2 System Responsiveness Index (SRI)

The magnitude of capacity adjustments is modulated by the SRI, which combines
two components (the third, a learning index $L_{\text{index}}$, is currently
omitted):

**Operational elasticity**  

$$
\varepsilon_{\text{ops}} = \frac{R \cdot (1 - S)}{1 + e^{-10(R - 0.2)}}$$

**Infrastructure pressure** – an exponential moving average of past stress:

$$
\rho_{\text{infra}}(t) = e^{-1/30} \cdot \rho_{\text{infra}}(t-1) + S(t)
$$

The SRI is computed with equal weights:

$$
\text{SRI} = 0.5 \cdot \varepsilon_{\text{ops}} + 0.5 \cdot \rho_{\text{infra}}
$$

### 4.3 Capacity update heuristic

Because the true gradient $\nabla_c \bar{E}$ is not directly available, we use a
**heuristic**:

- For each edge $a$, compute its stress deviation $(\text{stress}_a - S_{\text{avg}})$,
  where both $\text{stress}_a$ and $S_{\text{avg}}$ (their mean) are computed
  with the edges' *current* capacities.
- Multiply by the learning rate $\eta_s = 0.1$ and the current SRI value:

$$
g_{\text{short},a} = \eta_s \cdot (\text{stress}_a - S_{\text{avg}}) \cdot \text{SRI}
$$

Edges with **above‑average stress** receive a positive capacity increase;
edges with **below‑average stress** are slightly reduced. The capacities are
then clipped to physically plausible bounds.

Long‑term gradient and the antifragility gate are **not yet implemented**;
they will be added in a future version.

### 4.4 Running the iterative model

The script `luxembourg_example_eq_model.py` runs the static assessment
(§3), then `eq_model` with the settings in its `EQ_SETTINGS` dictionary:

```python
import scenario_config as cfg
from eq_model import eq_model

E_final, final_caps, history = eq_model(
    cfg.DISASTER_NET, cfg.SCENARIO_DIR,
    max_iterations=3, learning_rate=0.1,
    stress_window=cfg.STRESS_WINDOW, verbose=True,
)
```

From the command line, `--settings settings.json` overrides entries of
`EQ_SETTINGS` (windows as `[begin, end]` or `null`) and `--output DIR` chooses
the run folder:

```bash
python luxembourg_example_eq_model.py --settings settings.json --output runs/short
```

`eq_model(..., on_day=callback)` calls `callback` with each history entry as
soon as it is known; the script uses it to rewrite `run.json` after every day,
with the wall-clock time of each day in `day_seconds`.

`history` is a list of dicts, one per day (`day`, `E`, `M`, `R`, `C`, `S`, SRI
components, and mean/min/max capacity factor relative to base). The last entry
(`day == "final"`) describes the final simulation.

Each run is recorded in `eq_model_iter_logs/<timestamp>/` (ignored by git):

- `run.json` – all settings, provenance (commit and uncommitted-changes flag
  of this repo and the submodule, SUMO version), the static KPIs, the history
  and the final result. It is written before the loop starts (`"status":
  "running"`) and updated at the end (`"finished"`).
- `capacities.json` – the learned capacity of every edge.

Example output (note: produced **before** the 2026‑11‑17 fixes to how stress,
entropy, the disruption and $R$ are computed; these numbers need to be
regenerated):

```commandline
Performance:
 Duration: 1428.37s
 Real time factor: 63.0088
 UPS: 107697.373441
Vehicles:
 Inserted: 214073 (Loaded: 215526)
 Running: 0
 Waiting: 0
 Teleports: 140 (Collisions: 10, Jam: 60, Yield: 39, Wrong Lane: 31)
 Emergency Braking: 1235
DijkstraRouter answered 398310 queries and explored 1975.96 edges on average.
DijkstraRouter spent 335.98s answering queries (0.84ms on average).
Final E = 0.8889

Initial post-disaster E = 0.8489
Final (after learning)   E = 0.8889
Vulnerability after learning ΔE = -0.0537

Process finished with exit code 0
```

### 4.5 Algorithm 1 compliance checklist

The table below maps **Algorithm 1 (D2.3)** requirements to the current
implementation in `eq_model.py`.

| Algorithm 1 requirement | Status in `eq_model.py` | Notes                                                                                                                                                                                                                        |
|---|---|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Initialize `c0, E0, k=0, max_iter` | Partial | Capacities and iteration state are initialized, but the implementation uses scenario-driven defaults (`max_days`) rather than strict paper constants (e.g., `max_iter=1000`), to keep runtime manageable for full SUMO runs. |
| Tolerances `eps1..eps5` | Missing | A single `convergence_tol` was used as a simplification because explicit `f_k`, `m_k`, and cross-layer metrics are not yet exposed as vectors/scalars that can be norm-checked each iteration.                               |
| Layer 1 `f_k = Wardrop(c_k, q)` | Partial | We use SUMO’s dynamic assignment with rerouters, which is a reasonable proxy but does not expose a clean f_k vector matching the theory. Extracting a stable equilibrium flow per iteration would require a dedicated convergence check inside SUMO.                |
| Layer 2 `m_k = ModalSplit(f_k, U)` | Missing | The LuST scenario is car‑only for now; adding a modal split layer would require demand matrices for other modes and a calibrated utility function.                                                                           |
| KPI evaluation `x_k = ComputeKPIs(f_k, m_k)` | Partial | KPIs (`M, R, C, S`) are computed directly from SUMO outputs, but the pipeline is not yet refactored around explicit `f_k`/`m_k` state objects required by the algorithmic notation.                                          |
| Realized objective `E_k = E(x_k)` | Implemented | Computed via `performance_index_limited(...)`; this was prioritized because it is directly measurable from available SUMO-derived KPIs.                                                                                      |
| Target objective `Ebar_k` | Missing | A separate target function was deferred to avoid introducing an unvalidated objective without first defining how targets are calibrated against observed KPIs and scenario goals.                                            |
| Adaptive weight update `w_k = UpdateWeights(...)` | Missing | Fixed SRI weights were chosen for interpretability and reproducibility in early experiments; error-driven adaptive updates require a validated error signal and tuning strategy.                                             |
| Full SRI `w1*eps_ops + w2*rho_infra + w3*L_index` | Partial | `eps_ops` and `rho_infra` are implemented; `L_index` is omitted because long-horizon learning memory/experience variables are not yet defined in the current data model.                                                     |
| Short-term gradient `g_short` from `nabla_c Ebar_k` | Partial | A stress-deviation heuristic was used because analytic gradients are not available through SUMO black-box simulation; finite-difference gradients would require many extra runs per iteration.                               |
| Long-term gradient `g_long` with lag and AF gate | Missing | These need a database of past interventions and statistical tests, which are infrastructure components not yet present.                                                                   |
| Line search (`alpha` backtracking / Armijo) | Missing | Not added yet because each candidate `alpha` would trigger additional SUMO evaluations, substantially increasing runtime; this is planned after objective/gradient definitions are stabilized.                               |
| Projected update `Pi_C{c .* [1 + alpha(eta_s g_short + eta_l g_long)]}` | Partial | Multiplicative update and projection bounds are implemented for physical plausibility, but full form is incomplete until `alpha` line search and `g_long` are available.                                                     |
| Full stopping logic with all norms and consistency | Missing | Current stopping rule uses performance stabilization only; full criteria depend on explicit `f_k`, `m_k`, capacity norm tracking, and a defined cross-layer consistency metric.                                              |

This means the current model is a **reduced, heuristic implementation** inspired
by Algorithm 1, not a line-by-line complete translation.

## 5. API (`eqm_api/`)

A FastAPI back end over the functionality above, following the convention of
the ac-t4.5 `atc_api` (and `@rhoe/data`): routes under `/api`, lists as
`{items, total}` with `page`/`page_size`, ids as `_id` strings, and 422/409
errors as `{"code", "message"}` where a UI should explain them.

```bash
python -m pip install -r requirements.txt -r requirements-api.txt
uvicorn eqm_api.main:app --port 8000   # from the repository root, with SUMO available
# interactive docs: http://localhost:8000/docs
python -m pytest tests                 # includes the API tests (no SUMO needed)
```

| Method and path | What it does |
|---|---|
| `GET /api/health` | Liveness |
| `GET /api/scenario` | LuST network, horizon, closure window and closed edges, KPI windows, and whether the baseline/disaster outputs exist |
| `GET /api/impact-classes` | D3.2 Table 9 classes with their bounds |
| `POST /api/vulnerability` | $E$ for baseline and post-disaster KPIs, $\Delta E$ and the impact class. Full Tier‑2 index if both states give `Q` and `I`, else the limited index |
| `POST /api/simulations` | Queue the `baseline` (`get_benchmark.py`) or `disaster` (`sumo -c disaster.sumocfg`) run. **Replaces** that scenario's current outputs |
| `POST /api/assessments` | Queue the static assessment (`luxembourg_example.py`) with optional `stress_window` / `entropy_window` |
| `POST /api/eqm-runs` | Queue an EQ learning run (`luxembourg_example_eq_model.py`) with `name` and `settings` (defaults: `EQ_SETTINGS`) |
| `GET /api/{simulations,assessments,eqm-runs}` | List jobs (filter by `status`; simulations also by `scenario`) |
| `GET /api/{…}/{id}` | One job with its progress; assessments add `result`, EQ runs add `static`, `history`, `day_seconds`, `final`, `provenance` |
| `GET /api/eqm-runs/{id}/capacities` | Learned capacity per edge with `base` and `factor`, largest change first (`changed_only`, `min_change`, paging) |
| `GET /api/{…}/{id}/log` | Last lines of the job's output |
| `POST /api/{…}/{id}/cancel` | Cancel a queued or running job (409 if it already ended) |

Jobs run the existing scripts as local background processes in their own
process group (POSIX), so they survive an API reload and a restarted API
adopts them by PID. Everything is kept under `outputs/jobs/<id>/` (ignored by
git): `job.json`, `job.log`, for EQ runs `settings.json`, and `run/` with the
script's output (`result.json`, or `run.json` and `capacities.json`).

Progress comes from the outputs: SUMO's `Step #` log for simulations (fraction
of the 25 h and an ETA), and `run.json`, which the EQ script rewrites every day,
for EQ runs (stage, simulations done, the current day's fraction, ETA from the
mean day time).

All three kinds share one queue and run **one at a time** by default: they read
and write the same files in the scenario folder (the baseline/disaster outputs,
`outputs/iter.*`), so queue order matters, e.g. both simulations, then an
assessment or EQ run. A simulation counts as completed only if it rewrote all of
its output files, because `get_benchmark.py` does not pass on SUMO's exit code.

| Variable | Default | Meaning |
|---|---|---|
| `EQM_JOBS_DIR` | `outputs/jobs` | Where jobs are kept |
| `EQM_PYTHON` | the API's interpreter | Interpreter that runs the scripts |
| `EQM_MAX_RUNNING_JOBS` | `1` | Jobs running at once (keep 1, see above; tests use 0) |
| `EQM_CORS_ORIGINS` | `http://localhost:5180` | Browser origins allowed to call the API directly |
