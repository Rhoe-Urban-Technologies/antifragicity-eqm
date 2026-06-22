# Antifragicity Equilibrium Model

This repository contains code to replicate and extend the quantitative vulnerability
assessment described in deliverable **D3.2**.

## 1. KPI functions

The module `kpi_functions.py` computes four core network performance indicators
directly from SUMO simulation results. All indicators are normalised to $[0,1]$.

### 1.1 Mobility Throughput ($M$)

**Formula**  

$$ M = \frac{N_{\text{arrived}}}{N_{\text{total}}} $$

where  

- $N_{\text{arrived}}$ = number of trips with a valid `arrival` time,  
- $N_{\text{total}}$   = total number of `<tripinfo>` elements in the simulation output.

**SUMO data source** – `tripinfo.xml`  
The function `compute_throughput(tripinfo_file)` parses the XML, counts trips,
and returns the fraction that successfully completed.

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

---
### 1.3 Flow Entropy ($C$)

**Formula**  

Let $f_i$ be the number of trips for OD pair $i$, and $K$ the total number of
OD pairs with non‑zero flow.

$$ p_i = \frac{f_i}{\sum_j f_j} $$

$$ H = -\sum_i p_i \log_2 p_i \qquad\qquad
   H_{\max} = \log_2 K $$

$$ C = \frac{H}{H_{\max}} $$

$C = 0$ indicates all flow is concentrated on a single OD pair; $C = 1$
indicates a perfectly uniform distribution.

**SUMO data source** – `tripinfo.xml`  
The function `compute_entropy(tripinfo_file)` extracts the origin and destination
of every trip (from `departLane` / `arrivalLane` attributes), builds an OD
flow dictionary, then applies the Shannon entropy formula.

### 1.4 Stress Index ($S$)

**Formula (per edge $a$)**  

$$ \text{stress}_a = \min\!\left(
   \frac{f_a}{C_a} \cdot \left(1 - \frac{v_a}{v_{0,a}}\right),\;
   1.0
\right) $$

where  

- $f_a$   = vehicle count on edge $a$ during the simulation period,  
- $C_a$   = capacity of edge $a$ (number of lanes × 1800 veh/h),  
- $v_a$   = actual speed on edge $a$ (m/s),  
- $v_{0,a}$ = free‑flow speed of edge $a$ (m/s).

The overall stress index is the unweighted average over all non‑internal edges:

$$ S = \frac{1}{|A|} \sum_{a \in A} \text{stress}_a $$

**SUMO data sources**  

- **`edgedata.xml`** – flow (attribute `entered` or `left`) and, if available,
  speed (attribute `speed`). If speed is missing, the function falls back to
  the free‑flow speed (yielding zero stress).  
- **`.net.xml`** – lane count and free‑flow speed (`speed` attribute of the
  first lane of each edge).  

The function `compute_stress(net_file, edgedata_file)` internally calls
`load_edgedata` and `_load_edge_speeds` to collect the required measurements.

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

**Output** (matches the deliverable exactly):

E_base = 0.7250 E_post = 0.5167
System vulnerability ΔE = 0.287 → Moderate to Major vulnerability
Equity vulnerability ΔQ = 0.307 → Major equity vulnerability for elderly

## 3. Luxembourg LuST Scenario Vulnerability Assessment

We applied the same vulnerability methodology to the **LuST scenario**
(Luxembourg SUMO Traffic) by comparing a **baseline** simulation with a
**post‑disaster** simulation in which six critical edges are closed during the
morning peak (07:00–10:00) via a SUMO rerouter.

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

### 3.2 Steps to reproduce

#### 3.2.1 Install requirements

- Python 3.8+
- SUMO ≥ 1.20 (with `netconvert` and `sumo` on PATH)
- Python packages: `sumolib`, `networkx`, `matplotlib`

```bash
pip install sumolib networkx matplotlib
```

### 3.2.2 Follow the instructions in
`T4.1-KPIs/readme.md` to run the baseline and disaster simulations. This will generate output files in
`T4.1-KPIs/LuSTScenario-master/scenario/baseline` for the baseline run and `T4.1-KPIs/LuSTScenario-master/scenario/outputs` for the "post-disaster" run.

### 3.2.3 Run `luxembourg_example.py` to compute KPIs and vulnerability

```bash
python luxembourg_example.py
```

## 4. Iterative Equilibrium Model

Beyond the static vulnerability comparison, the **Three‑Layer Equilibrium Model**
(D2.3, Algorithm 1) introduces a learning loop that allows the network to
**adapt its capacities over multiple “days”** in order to improve the performance
index after a disruption. This is implemented in `eq_model.py`.

### 4.1 How the learning loop works

The loop starts from the **post‑disaster** capacities (the same base network,
with closures applied via the rerouter). Each iteration (one “day”) performs:

1. **Apply current capacities** – writes a modified SUMO network where lane
   speeds are scaled proportionally to the capacity ratio (a proxy for changing
   throughput).
2. **Run SUMO simulation** – same route files and rerouter closures as the
   disaster run.
3. **Compute core KPIs** – $M, R, C, S$ from the resulting `tripinfo.xml` and
   `edgedata.xml`.
4. **Update SRI components** – operational elasticity and infrastructure pressure.
5. **Calculate per‑edge stress** and a **short‑term capacity gradient** $g_{\text{short}}$.
6. **Update capacities** – multiply each edge’s capacity by $(1 + g_{\text{short}})$,
   then clip to $[0.5 \times \text{base}, 2.0 \times \text{base}]$.
7. **Check convergence** – stop when the performance index $E$ stabilises for
   five consecutive days.

The final configuration is the **antifragile equilibrium** for the given disruption.

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

- For each edge $a$, compute its stress deviation $(\text{stress}_a - S_{\text{avg}})$.
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

The script `luxembourg_example_eq_model.py` calls `eq_model` after
the static vulnerability assessment. Example:

```python
from eq_model import eq_model

E_final, final_caps, history = eq_model(
    DISASTER_NET, scenario_dir,
    max_iterations=3, learning_rate=0.1, verbose=True
)
```

Example output:

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
