# ChangeGuard: Pre-Deployment Data Contract Sentinel

![ChangeGuard](assets/changeguard-thumbnail.png)

**ChangeGuard** is an autonomous agent that prevents breaking data contract changes before they reach production. It reads DataHub's lineage graph through the official DataHub MCP Server, traces column-level downstream impact, scores risk with transparent rules, and produces a BLOCK/ALLOW deployment decision with a concrete migration checklist.

Built for **[Build with DataHub: The Agent Hackathon](https://datahub.devpost.com/)** — Category: *Agents That Do Real Work*.

**Live public demo:** [https://changeguard-sentinel.streamlit.app](https://changeguard-sentinel.streamlit.app)

---

## What ChangeGuard Does

Schema changes (renaming a column, dropping a field, changing a type) routinely break dashboards, downstream tables, and ML pipelines without warning, because nobody traces the blast radius before deploying. ChangeGuard closes that gap: given a proposed change, it asks DataHub what actually depends on that column, scores the risk with visible, reproducible rules, and tells you whether it's safe to ship.

---

## Architecture: ChangeGuard → MCP → DataHub

```
┌──────────────────────────────────────────────────────────────────────┐
│                        PROPOSED SCHEMA CHANGE                        │
│              rename commerce.orders.customer_id → cust_key          │
└───────────────────────────────┬──────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│              CHANGEGUARD AGENT (contract_sentinel/agent.py)          │
│                                                                      │
│  Step 1  → parse_change                                              │
│  Step 2  → resolve_urn                                               │
│  Step 3  → validate_schema                                           │
│  Step 4  → fetch_previous_context                                    │
│  Step 5  → fetch_lineage                                             │
│  Step 6  → fetch_potential_downstream                                │
│  Step 7  → assess_risk                                               │
│  Step 8  → generate_report                                           │
│  Step 9  → writeback                                                 │
│  Step 10 → decision                                                  │
│  Step 11 → persist_decision                                          │
│                                                                      │
└───────────────────────────────┬──────────────────────────────────────┘
                                │  MCP stdio (uvx mcp-server-datahub)
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│              DataHub MCP Server (acryldata/mcp-server-datahub)       │
│  search · list_schema_fields · get_entities · get_lineage            │
│  save_document (optional, when advertised and explicitly confirmed)  │
└───────────────────────────────┬──────────────────────────────────────┘
                                │  HTTP (GMS API)
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│                 DataHub (metadata catalog + lineage graph)           │
└──────────────────────────────────────────────────────────────────────┘
```

In Live mode, DataHub reads go through the official, open-source
**`mcp-server-datahub`** package (https://github.com/acryldata/mcp-server-datahub),
launched as a local subprocess over stdio via `uvx`. `search` resolves the
dataset, `list_schema_fields` validates the schema, `get_entities` reads the
last persisted ChangeGuard context, and `get_lineage` supplies both confirmed
column impact and informational table-level propagation. Step 9 can use the
optional `save_document` tool to write the Markdown report when that tool is
advertised and writeback is explicitly confirmed.

Step 11, custom-property decision persistence, does **not** use MCP or
`save_document`. It uses the official `acryl-datahub` Python SDK's REST
emitter directly against DataHub's GMS API (see
[DataHub Writeback](#datahub-writeback) below).

---

## Why ChangeGuard is different from DataHub's built-in impact analysis

DataHub already provides a context graph — lineage, ownership, schemas,
and (in DataHub Cloud) its own impact analysis view. ChangeGuard does not
try to replace that. Instead, it uses DataHub's graph as an input and
turns it into a repeatable pre-deployment check:

- **DataHub provides the graph.** Lineage and column-level relationships
  come entirely from DataHub via `search` and `get_lineage` — ChangeGuard
  does not maintain its own copy of the graph.
- **ChangeGuard turns that graph into a policy decision.** Given one
  proposed change, it applies the same transparent scoring rules every
  time and produces exactly one of two outcomes: `ALLOW` or `BLOCK`. This
  is deterministic and reproducible — the same input always produces the
  same score (see [Risk Scoring Transparency](#risk-scoring-transparency)).
- **The decision is automatable, not just a dashboard view.** Because the
  agent is a small Python library (`contract_sentinel`) with a plain
  `Change` → `AgentResult` interface, the same evaluation used by the
  Streamlit UI can be called from a script — see
  [`demo.py`](demo.py) for the CLI form of this.
- **It keeps evidence, not just a live graph snapshot.** Optionally (with
  explicit confirmation), the decision — ALLOW/BLOCK, score, severity,
  operation, column, timestamp — is written back onto the dataset in
  DataHub as custom properties, so the next person or agent inspecting
  that dataset can see what was evaluated and decided, without re-running
  anything. This has been verified against a real local DataHub instance
  (see [DataHub Writeback](#datahub-writeback)).

---

## Demo Mode vs Live Mode

The app has two distinct data sources, selectable in the sidebar:

| | **Demo (fixtures)** | **Live (DataHub MCP)** |
|---|---|---|
| Data source | Reproducible in-memory fixtures (`contract_sentinel/fixtures.py`) | A real DataHub instance, queried live via MCP |
| Requires DataHub running? | No | Yes |
| Requires `uvx`/`uv` installed? | No | Yes |
| On connection/query failure | N/A | Shows the real error. **Never falls back to fixtures or demo data.** |
| Where it works | Anywhere, including the public Streamlit Cloud deployment | Only where the DataHub instance and MCP server are reachable |

This distinction is enforced in code, not just in the UI: in Live mode, if the MCP connection fails or a tool call errors, `contract_sentinel/agent.py` returns the failure immediately — it does not substitute `SHOWCASE_ASSETS` or any other fixture data. This is covered by dedicated tests (see [`tests/test_agent.py`](tests/test_agent.py), `LiveModeShapeTests`).

### Important: the public Streamlit app is Demo-only

**[https://changeguard-sentinel.streamlit.app](https://changeguard-sentinel.streamlit.app)** runs on Streamlit Community Cloud, which has no network path to a DataHub instance running on `localhost` on someone's own machine. The public deployment is meant to showcase the agent pipeline, the risk-scoring logic, and the reporting/UI using Demo mode.

**Live mode against real DataHub is demonstrated locally** (see below) — this is what the submission video shows, and it is what a judge running the project on their own machine with their own DataHub instance would also see.

---

## DataHub MCP Integration

ChangeGuard uses the official **`mcp-server-datahub`** (PyPI package, launched via `uvx`) over stdio. On connect, `MCPStdioClient` performs the full MCP handshake (`initialize` → `notifications/initialized` → `tools/list`) before issuing any tool call.

The MCP server must advertise these tools, or the connection fails fast
with a clear error (`contract_sentinel/datahub_mcp.py`, `REQUIRED_TOOLS`):

| MCP Tool | Called by the agent? | Purpose |
|---|---|---|
| `search` | **Yes**, every Live run | Find the dataset by name → resolve URN |
| `get_lineage` | **Yes**, every Live run | Column-level downstream traversal |
| `list_schema_fields` | **Yes**, every Live run | Verify the dataset and column exist before scoring |
| `get_entities` | **Yes**, every Live run | Read persisted ChangeGuard context and entity metadata available through MCP (see [DataHub Memory](#datahub-memory) below) |
| `get_lineage_paths_between` | Required for connection, not currently called by the agent | Reserved for future multi-hop path tracing |
| `save_document` | Only if "Write report back to DataHub" is checked and available | Write the Markdown impact report as a document |

Today, `search`, `get_lineage`, `list_schema_fields`, and `get_entities`
are what the agent actually calls to produce a decision and surface
context. `get_lineage_paths_between` is required at connection time (so
an incompatible MCP server is rejected early) but is not yet invoked in
the pipeline — this is accurately reflected as a gap, not a used feature.

As observed against a real `mcp-server-datahub` server, `get_entities`'s
response does not include `ownership`, `domain`, or `tags` today — only
`platform`, `properties` (including `customProperties`), `health`,
`schemaMetadata`, and `relatedDocuments`. ChangeGuard therefore uses it
specifically to read back `customProperties`, not for ownership/domain/tag
enrichment (see [DataHub Memory](#datahub-memory)).

`save_document` is a Document Tool, not a Mutation Tool, and `mcp-server-datahub` automatically hides it when the DataHub instance has no documents yet in its catalog. ChangeGuard treats it as optional: Live mode connects and runs the full search → lineage → risk → decision flow even when `save_document` is unavailable. If it is unavailable, the "Write report back to DataHub" step is skipped with a clear reason, instead of silently failing or blocking the connection.

### Writeback Safety

Both writeback mechanisms — the optional `save_document` report and the
custom-property decision persistence described in
[DataHub Writeback](#datahub-writeback) — always require explicit user
confirmation (a checkbox in the UI / `confirm_writeback=True` in code)
before anything is written. If the user does not confirm, nothing is
written to DataHub.

---

## Quick Start

### Prerequisites

- Python 3.11+
- No paid API keys required (deterministic agent, no LLM dependency)

### Run the Agent (CLI, Demo mode)

```bash
git clone https://github.com/javierbolivia/changeguard-datahub.git
cd changeguard-datahub
python demo.py
```

### Run the Visual Interface (Demo mode, no setup)

```bash
pip install -r requirements.txt
streamlit run app.py
```

### Run Tests

```bash
python -m unittest discover -s tests -v
```

### Run Against Real DataHub (Live Mode, local)

1. Install `uv` (provides `uvx`), used to launch the official MCP server:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

2. Start DataHub locally with Docker ([Quickstart Guide](https://datahubproject.io/docs/quickstart)):

```bash
pip install acryl-datahub
python -m datahub docker quickstart
```

This brings up DataHub's GMS API at `http://localhost:8080` and the frontend at `http://localhost:9002`.

3. Populate the catalog with at least one dataset (the quickstart's own `ingest-sample-data` command may fail depending on your Python version; ingesting your own metadata via the DataHub SDK, or connecting to real production metadata, both work).

4. In the Streamlit sidebar, select **Live (DataHub MCP)**, confirm the DataHub GMS URL (default `http://localhost:8080`), and optionally provide a personal access token if your instance has authentication enabled.

5. Run the agent. On the first run, `uvx` downloads `mcp-server-datahub` automatically — no manual installation needed. See [`contract_sentinel/mcp_connection.py`](contract_sentinel/mcp_connection.py) for the connection implementation and [`examples/datahub-mcp.example.json`](examples/datahub-mcp.example.json) for an MCP client config template.

### Verified Real Example Setup

The Live pipeline has been verified end-to-end against a real local DataHub instance seeded with:

- `commerce.orders` (`order_id`, `customer_id`, `amount`)
- `analytics.customer_orders` (`customer_id`)
- `analytics.sales_summary`
- Lineage: `commerce.orders` → `analytics.customer_orders` → `analytics.sales_summary`, including column-level lineage on `customer_id`

See [Verified Live Scenarios](#verified-live-scenarios) below for the two
documented runs against this seeded data.

---

## Verified Live Scenarios

Both scenarios below were run against the real local DataHub setup
described above, in Live mode, with no fixture data involved. Full
details (including the raw agent step results) are in the linked files.

| Scenario | Operation | Risk Score | Severity | Decision | Details |
|---|---|---|---|---|---|
| Safe Rename | `rename` `customer_id` → `cust_key` | **50/100** | **MEDIUM** | **ALLOW** | [`examples/rename_allow_live.md`](examples/rename_allow_live.md) |
| Dangerous Drop | `drop` `customer_id` | **60/100** | **HIGH** | **BLOCK** | [`examples/drop_block_live.md`](examples/drop_block_live.md) |

Both scenarios found the same real downstream asset
(`analytics.customer_orders`, discovered live via `get_lineage`); the
score difference comes entirely from the base risk of the operation
itself (`drop` = 55 base points vs `rename` = 45), which is enough to
cross the BLOCK threshold. See the linked files for the full "why
flagged" reasoning and migration checklist for each.

---

## DataHub Writeback

ChangeGuard can persist its decision onto the analyzed dataset in
DataHub, so a later viewer of that dataset — human or agent — can see
what was evaluated without re-running the agent. This is implemented in
[`contract_sentinel/datahub_writeback.py`](contract_sentinel/datahub_writeback.py)
and is a separate mechanism from the optional `save_document` report
described above.

**Mechanism:** Custom Properties on the dataset's `datasetProperties`
aspect, written via the official `acryl-datahub` Python SDK
(`DatahubRestEmitter` + `DatasetPatchBuilder`) directly against DataHub's
GMS REST API — not through MCP. This was chosen because it does not
depend on `save_document`/mutation tools being enabled or available on
the MCP server, requires no new schema registration (unlike DataHub
Structured Properties), and applies as a JSON patch rather than
overwriting the dataset's existing properties or description.

**What gets written**, confirmed against a real run (the Dangerous Drop
scenario above):

```
changeguard_decision: BLOCK
changeguard_risk_score: 60
changeguard_severity: high
changeguard_operation: drop
changeguard_column: customer_id
changeguard_timestamp: 2026-08-08T13:14:04+00:00
```

**Confirmation required.** This only happens if the "Persist decision to
DataHub" checkbox is checked before running the agent in Live mode. If
not confirmed, the corresponding pipeline step (`persist_decision`) is
skipped and nothing is written — enforced in
[`contract_sentinel/agent.py`](contract_sentinel/agent.py) and covered by
tests in [`tests/test_agent.py`](tests/test_agent.py).

**How this was verified:** the write was performed against the real
local DataHub instance at `http://localhost:8080`, then read back
independently using the DataHub SDK's graph client
(`DataHubGraph.get_aspect`) — not by trusting the write call's return
value. The six `changeguard_*` properties matched what was written, and
the dataset's original `description` was confirmed unchanged, showing the
patch is additive. This was also confirmed through the full agent
pipeline in Live mode (`persist_decision` step reaching `SUCCESS`), not
only through the standalone writeback function.

**Where to see it:** open `http://localhost:9002`, search for
`commerce.orders`, open the dataset, and check the **Properties** tab —
the `changeguard_*` custom properties will be listed there.

---

## DataHub Memory

ChangeGuard also reads back the `changeguard_*` custom properties it
writes (see [DataHub Writeback](#datahub-writeback) above), via the MCP
`get_entities` tool, and surfaces them as **the last ChangeGuard decision
persisted in DataHub** for the dataset being evaluated.

This is explicitly **not** a history or audit trail: custom properties
are overwritten on every writeback, so only the single most recent
decision is ever available this way. It is implemented in
[`contract_sentinel/datahub_writeback.py`](contract_sentinel/datahub_writeback.py)
(`parse_persisted_context`) and read in
[`contract_sentinel/agent.py`](contract_sentinel/agent.py) as a
best-effort step (`fetch_previous_context`).

**Behavior:**
- If a dataset has no `changeguard_*` properties yet, `previous_context`
  is `None` and the report simply notes no previous decision was found —
  this is not an error.
- If the current dataset/column/operation match the persisted context
  exactly, the Streamlit UI shows a **"Previously evaluated in
  DataHub"** badge — but the agent always re-runs the full analysis
  (schema validation, lineage, scoring) regardless. The previous
  decision is context for the operator, never a shortcut and never an
  input to the current score.
- If `get_entities` fails or DataHub is briefly unreachable, this step is
  marked `FAILED` but does not block schema validation, lineage, or the
  ALLOW/BLOCK decision — DataHub Memory is best-effort by design.

---

## CI/CD Gate

ChangeGuard exposes a CI-compatible exit code through `contract_sentinel/cli.py`, so a schema change can be gated in a pipeline without going through the Streamlit UI. It reuses the exact same `ChangeGuardAgent` and DataHub MCP integration as the UI — no separate scoring logic.

```bash
python -m contract_sentinel.cli \
  --dataset commerce.orders \
  --column customer_id \
  --operation drop \
  --mode live \
  --datahub-url http://localhost:8080 \
  --json
```

- **exit 0** = `ALLOW`
- **exit 1** = `BLOCK`
- **exit 2** = execution/configuration error (dataset not found, column not found, DataHub/MCP unreachable, invalid arguments)

The CLI never writes back to DataHub (`confirm_writeback=False` always), so it is safe to run unattended in CI. See [`examples/github-actions-gate.yml`](examples/github-actions-gate.yml) for a small conceptual GitHub Actions workflow example — it is not wired into this repository's own Actions.

---

## Project Structure

```
changeguard-datahub/
├── app.py                          # Streamlit UI: Demo + Live modes
├── demo.py                         # CLI demo (Demo mode, no dependencies)
├── contract_sentinel/
│   ├── __init__.py                 # Package exports
│   ├── cli.py                      # CI/CD gate CLI (0=ALLOW, 1=BLOCK, 2=ERROR)
│   ├── agent.py                    # Autonomous 11-step agent pipeline
│   ├── risk.py                     # Transparent risk scoring engine
│   ├── report.py                   # Markdown report generator
│   ├── fixtures.py                 # Reproducible demo metadata
│   ├── datahub_mcp.py               # MCP tool boundary (required vs optional tools)
│   ├── mcp_connection.py           # Live MCP stdio client (uvx mcp-server-datahub)
│   └── datahub_writeback.py        # Persists decisions as DataHub custom properties (SDK, not MCP)
├── tests/
│   ├── test_agent.py                # Agent pipeline + real MCP response-shape tests
│   ├── test_cli.py                  # CI/CD gate CLI tests (exit codes, JSON, no duplicated engine)
│   ├── test_risk.py                # Risk scoring tests
│   ├── test_report.py              # Report generation tests
│   ├── test_datahub_mcp.py         # MCP adapter tests (incl. optional save_document)
│   └── test_datahub_writeback.py   # Writeback property-building tests
├── examples/
│   ├── changeguard-impact-report.md   # Sample agent output (Demo mode)
│   ├── rename_allow_live.md           # Verified Live scenario: ALLOW
│   ├── drop_block_live.md             # Verified Live scenario: BLOCK
│   ├── datahub-mcp.example.json       # MCP server config template
│   └── github-actions-gate.yml        # Conceptual CI/CD gate workflow example
├── requirements.txt
├── LICENSE                         # Apache 2.0
└── README.md
```

---

## Risk Scoring Transparency

ChangeGuard uses **deterministic, explainable rules** instead of opaque LLM judgments. Every factor contributing to the score is visible:

| Factor | Points |
|--------|--------|
| `drop` operation | 55 base |
| `rename` operation | 45 base |
| `type_change` operation | 35 base |
| `add` operation | 5 base |
| Per downstream asset (capped at 25) | +5 |
| Per critical asset (capped at 20) | +10 |
| Per business dashboard (capped at 10) | +5 |

**Severity thresholds:** Critical (≥80) | High (≥60) | Medium (≥30) | Low (<30)

**Deployment decision:** the agent only outputs two decisions, `BLOCK` or `ALLOW` — there is no separate "warn" state. Critical and High severity → **BLOCK** (the migration checklist must be completed first). Medium and Low severity → **ALLOW** ("change may proceed"), meaning the change is not blocked but still carries risk: review the "Why Flagged" reasons and the migration checklist before deploying, especially at Medium severity.

---

## Example Output (Demo Mode)

The included CLI demo evaluates a proposed `rename` of `commerce.orders.customer_id` against fixture metadata. The agent discovers:

- **3 downstream consumers** (fixture data)
- **2 critical assets** (Revenue Dashboard, Customer LTV dataset)
- **Risk score: 90/100** → Deployment BLOCKED
- Generated migration checklist with 5 action items

See the full output: [`examples/changeguard-impact-report.md`](examples/changeguard-impact-report.md)

---

## Why ChangeGuard?

| Problem | ChangeGuard Solution |
|---------|---------------------|
| Schema changes break downstream silently | Agent traces the real blast radius via DataHub lineage before deploy |
| No visibility into what's affected | Surfaces the real downstream datasets from DataHub's lineage graph (ownership/criticality metadata is not yet included — see [Limitations](#limitations)) |
| Risk is assessed subjectively | Transparent scoring with visible, testable factors |
| Knowledge stays in one person's head | Can persist the decision (or the full report) back onto the dataset in DataHub |
| Requires expensive LLM APIs | Deterministic rules — free and reproducible |

---

## Technologies

- **Python 3.11+** — Core implementation
- **DataHub MCP Server** (`mcp-server-datahub`, via `uvx`) — Live metadata access
- **Streamlit** — Interactive demo/Live interface
- **Pydantic** — Data validation
- **NetworkX** — Graph traversal utilities

---

## Limitations

- The public Streamlit Cloud deployment only supports Demo mode (see above) — it cannot reach a DataHub instance on your local machine.
- Live mode stops with a clear error when `search` returns no matching dataset. It never constructs a guessed URN or substitutes fixture data for a missing dataset or a failed DataHub/MCP call.
- DataHub Memory stores only the last persisted ChangeGuard decision. Custom properties are overwritten on each writeback, so this is not a historical audit trail.
- `save_document` writeback depends on the target DataHub instance already having at least one document in its catalog, or being configured to expose the tool regardless; ChangeGuard detects this at connect time and skips that step with a clear reason rather than failing.
- The agent requires `get_lineage_paths_between` to be advertised by the MCP server to connect, but does not currently call it — see [DataHub MCP Integration](#datahub-mcp-integration).
- `get_entities` does not return `ownership`, `domain`, or `tags` in the current `mcp-server-datahub` response shape, and our seeded local DataHub instance has none of these set on any dataset either (confirmed directly via DataHub's GraphQL API during investigation). ChangeGuard therefore does not display ownership/domain/tags for Live-mode assets — see [DataHub Memory](#datahub-memory) for what `get_entities` is used for instead.
- Ownership, criticality, and dashboard classification of downstream assets in Live mode are not yet sourced from DataHub — `critical` is always `false` and `owner` is always `"Unknown"` for Live-mode results, since the agent does not call the tools that would supply that data. This means the risk score's "critical asset" and "business dashboard" bonus factors never trigger in Live mode today (they only apply in Demo mode, where fixtures set `critical: true` on some assets).
- Potential downstream propagation depends on table-level `get_lineage`. An incomplete table-level graph can omit potential assets, and this informational call has been observed to time out intermittently on the local development machine; its failure does not block scoring or the final decision.
- On Windows, a Live CLI run may emit an `asyncio`/`ResourceWarning` during interpreter shutdown after the result has already been printed. The warning does not change the result or exit code.
- The CI/CD gate (`contract_sentinel/cli.py`, see [CI/CD Gate](#cicd-gate) below) never writes back to DataHub — it always runs with `confirm_writeback=False`, so it is safe to run unattended, but it cannot be used to persist a decision the way the Streamlit UI can.
- The decision-persistence writeback (custom properties) has been verified against a local DataHub OSS quickstart instance only, not against DataHub Cloud or a production deployment.
- There is no Slack or Jira integration, and no LLM is used anywhere in the decision pipeline.

---

## License

[Apache License 2.0](LICENSE)

---

## Team

Built by [@javierbolivia](https://github.com/javierbolivia) for the DataHub Agent Hackathon 2026.
