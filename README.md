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
`T4.1-KPIs/LuSTScenario-master/scenario/outputs`.

### 3.2.3 Run `luxembourg_example.py` to compute KPIs and vulnerability

```bash
python luxembourg_example.py
```