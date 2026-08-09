# ChangeGuard — Codex Handoff

This document records the verified state of ChangeGuard immediately before the
final documentation commit. Permanent development constraints remain in
`AGENTS.md`; this handoff provides current implementation and submission
context.

## Stable baseline

- Branch: `main`
- Stable HEAD before the documentation commit:
  `fddbd2686483dcca792a18737f720191193f4712`
- Test suite: **85/85 passing**
- Product position: **pre-deployment schema change gate**
- Implementation status: controlled hackathon implementation, not a
  production-hardened deployment

## Product summary

ChangeGuard is a deterministic agentic workflow that evaluates a proposed
schema change before deployment. It validates the source schema, reads DataHub
lineage evidence, distinguishes confirmed column impact from potential table
propagation, and applies transparent policy rules to produce an ALLOW/BLOCK
decision. It also produces deterministic remediation recommendations and can,
with explicit user confirmation, persist the latest decision or a full report
back to DataHub.

Primary pitch:

> ChangeGuard turns DataHub lineage into a pre-deployment ALLOW/BLOCK gate, with evidence-aware blast radius and remediation before schemas break production.

The decision path does not use an LLM. Remediation does not modify the score or
guarantee that a change is safe; it is a recommended path to re-evaluation.

## Current feature set

- Source and rename-target schema validation
- DataHub Memory for the latest persisted ChangeGuard decision
- Confirmed column-level and potential table-level lineage views
- Centralized deterministic risk scoring
- ALLOW/BLOCK gate decisions
- Deterministic, operation-specific remediation recommendations
- CLI/CI exit-code contract and valid JSON output
- Warning/degraded presentation for best-effort metadata failures
- Hardened MCP stdio transport and subprocess lifecycle
- Optional report and decision writeback with explicit user confirmation

These are current capabilities. Anything in [Future work](#future-work) is not
implemented.

## Eleven-step pipeline

`ChangeGuardAgent` in `contract_sentinel/agent.py` is the central engine and the
only caller of `assess_change`:

1. Parse and validate the proposed change (`parse_change`).
2. Resolve the dataset URN with MCP `search` (`resolve_urn`).
3. Validate source and target schema with `list_schema_fields`
   (`validate_schema`).
4. Read the latest persisted context with `get_entities`
   (`fetch_previous_context`, best-effort).
5. Fetch confirmed column-level lineage with column-filtered `get_lineage`
   (`fetch_lineage`).
6. Fetch potential table-level propagation with unfiltered `get_lineage`
   (`fetch_potential_downstream`, best-effort and unscored).
7. Calculate risk through `contract_sentinel/risk.py` (`assess_risk`).
8. Generate the Markdown report (`generate_report`).
9. Optionally write the report with MCP `save_document` (`writeback`).
10. Produce the ALLOW/BLOCK decision (`decision`).
11. Optionally persist the latest decision through the DataHub SDK
    (`persist_decision`).

Best-effort failures are surfaced as warnings/degraded analysis rather than
being presented as successful evidence. Live failures are never replaced with
Demo fixtures.

## DataHub integration

### MCP reads and optional report write

| Tool | Current use |
|---|---|
| `search` | Resolve the requested dataset. |
| `list_schema_fields` | Validate the source column and a rename target. |
| `get_entities` | Read the latest persisted ChangeGuard Custom Properties. |
| `get_lineage` with `column` | Fetch confirmed column-level downstream evidence. |
| `get_lineage` without `column` | Fetch potential table-level propagation. |
| `save_document` | Optionally write the full Markdown report when advertised and explicitly confirmed. |

`get_lineage_paths_between` is required by the adapter's connection contract
but is not called by the current pipeline. Do not present it as an active
feature.

### SDK decision persistence

The separate `persist_decision` step uses the official `acryl-datahub` Python
SDK, not MCP `save_document`. It writes six Custom Properties:

- `changeguard_decision`
- `changeguard_risk_score`
- `changeguard_severity`
- `changeguard_operation`
- `changeguard_column`
- `changeguard_timestamp`

The Streamlit UI requires explicit writeback confirmation before either the
report or decision persistence can run. The CLI always uses
`confirm_writeback=False` and does not write to DataHub.

## Confirmed versus potential

**Confirmed impact** means DataHub returned explicit column-level lineage
evidence. Confirmed downstream assets may contribute to the score according to
the current risk policy.

**Potential propagation** means table-level downstream propagation exists but
column-level impact is not confirmed. Potential assets are visible for review
and remediation planning, are de-duplicated against confirmed assets, and do
not increase the risk score. They must not be described as broken or impacted
with certainty.

This distinction is evidence-aware, not a claim that DataHub's lineage graph is
complete.

## DataHub Memory

DataHub Memory means only the **latest persisted ChangeGuard decision**. The six
Custom Properties are overwritten on each persistence operation, so Memory is
not history, an audit trail, or a decision log.

A later run reads this prior context through `get_entities`, then still performs
fresh schema validation, lineage retrieval, and risk assessment. A matching
dataset, column, and operation may produce a “Previously evaluated in DataHub”
indicator, but previous context never changes the current score.

Failure to read prior context is best-effort: it is shown as degraded/failed
metadata retrieval and the current analysis continues without prior context.

## Verified local Live scenarios

The following scenarios use the seeded local DataHub environment, not fixtures
and not the public hosted application.

### Safe Rename

- Dataset: `commerce.orders`
- Column: `customer_id`
- Operation: `rename`
- New name: `cust_key`
- Result: **50/100 — MEDIUM — ALLOW**
- Confirmed: `analytics.customer_orders`
- Potential: `analytics.sales_summary` (informational and unscored)

### Dangerous Drop

- Dataset: `commerce.orders`
- Column: `customer_id`
- Operation: `drop`
- Result: **60/100 — HIGH — BLOCK**
- Confirmed: `analytics.customer_orders`
- Potential: `analytics.sales_summary` (informational and unscored)

The scores are produced by the real pipeline, not hardcoded scenario outputs.
With exactly one confirmed downstream consumer, the current policy adds five
points to the 45-point rename or 55-point drop base weight.

## Public Demo versus local Live

- **Public hosted app:** Demo mode only. It has no network path to the local
  DataHub backend and must not be described as publicly providing Live DataHub
  integration.
- **Local development and submission video:** Live mode against the real local
  DataHub environment. The Live Safe Rename and Dangerous Drop reference
  scenarios demonstrated in the video use this environment.

Live mode surfaces connection and query errors and never substitutes Demo
fixtures.

## Remediation

`contract_sentinel/remediation.py` centralizes deterministic remediation plan
construction. The same structured plan is consumed by Streamlit, CLI/JSON, and
the Markdown report.

The plan is derived from the current change, decision, and confirmed/potential
evidence. It does not use an LLM, change scoring, or guarantee safety. Describe
it as remediation recommendations or a recommended path to re-evaluation.

## CLI-compatible CI gate

Run the gate with `python -m contract_sentinel.cli`. It reuses
`ChangeGuardAgent` and does not contain a separate scoring engine.

- Exit `0`: ALLOW
- Exit `1`: BLOCK
- Exit `2`: execution or configuration ERROR

`--json` returns valid JSON for success, BLOCK, and error behavior. The CLI
never writes back. `examples/github-actions-gate.yml` is a GitHub Actions
workflow example demonstrating the exit-code contract; it is not a claim that
production CI integration is deployed.

## MCP reliability state

The MCP stdio lifecycle in `contract_sentinel/mcp_connection.py` has a dedicated
reliability fix and tests:

- Child stderr is continuously drained into a bounded diagnostic tail.
- A pending future is registered before each JSON-RPC request write.
- Pending futures are removed on completion, error, timeout, and cancellation.
- Reader failures propagate immediately to pending requests.
- New requests are rejected after reader failure or shutdown begins.
- Initialization failure cleans up the subprocess.
- The existing terminate/wait/kill fallback remains bounded.
- No request or startup timeout value was changed.

Four sequential Live analyses—DROP, RENAME, DROP, RENAME—completed with the
expected results. No orphan `uvx`/`mcp-server-datahub` processes remained, and
the Windows asyncio shutdown warning was not reproduced after the fix.

The current command launches `mcp-server-datahub@latest`; the MCP server version
is not pinned.

## Current limitations

- DataHub Memory is latest state only, not historical storage.
- Incomplete lineage can produce incomplete evidence; potential propagation is
  not confirmed column impact.
- Scoring weights are transparent policy choices, not calibrated
  probabilities. Ownership, domain, tags, and criticality do not drive Live
  scoring today.
- The hosted app is Demo-only, and the controlled implementation is not
  production-hardened. SDK persistence identity assumptions are verified for
  the seeded Snowflake/PROD local scenario.

## Final Submission Copy

### A. One-line tagline

ChangeGuard turns DataHub lineage into a pre-deployment ALLOW/BLOCK gate, with
evidence-aware blast radius and remediation before schemas break production.

### B. Three-sentence description

ChangeGuard evaluates proposed schema changes before deployment by resolving
the dataset in DataHub, validating its schema, and tracing confirmed column
consumers. It separates proven column impact from potential table propagation,
then applies deterministic policy to produce an ALLOW/BLOCK decision and
operation-specific remediation through the same UI and CI-compatible engine.
With explicit user confirmation, it can persist the latest decision or full
report to DataHub while every future risk assessment is recomputed from fresh
metadata evidence.

### C. Feature bullets

- Live schema validation and lineage retrieval through the official DataHub MCP
  Server
- Evidence-aware separation of confirmed and potential downstream propagation
- Transparent deterministic ALLOW/BLOCK policy
- Operation-specific remediation recommendations
- Shared Streamlit and CLI/CI-compatible engine with `0`/`1`/`2` exit codes
- Optional latest-decision and full-report writeback with explicit confirmation

### D. Truthful limitations and notes

- The public hosted app is Demo-only; verified Live scenarios run against local
  DataHub.
- DataHub Memory stores only the latest persisted decision, not history.
- Results depend on available lineage evidence, and scoring weights are policy
  choices rather than calibrated probabilities.
- This is a controlled hackathon implementation, not a production-hardened
  deployment.

## Future work

The following items are not current capabilities:

- Pin and lock the MCP server/dependency environment for reproducibility.
- Preserve resolved entity identity end-to-end across broader DataHub platform
  and environment combinations.
- Harden URL, token, and shared-deployment security boundaries.
- Calibrate policy using observed incidents and add an explicit
  insufficient-evidence/UNKNOWN policy if desired.
- Parse real migration artifacts and connect the workflow example to a chosen
  production CI system.

Do not present future work as implemented functionality.
