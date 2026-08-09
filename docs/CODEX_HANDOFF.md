# ChangeGuard — Codex Handoff

This document describes the real, current state of the ChangeGuard
project as of commit `c36b321` on `main`, so a new agent can continue
work without reading prior chat history. See `AGENTS.md` in the repo root
for the permanent development rules — this document is context, not
rules.

## 1. Product

ChangeGuard is a pre-deployment schema/data-contract safety agent built
on DataHub. It evaluates a proposed schema change and produces a
deterministic ALLOW/BLOCK decision before the change ships.

Current pipeline (`ChangeGuardAgent.run_async`, `contract_sentinel/agent.py`):

1. Parse & validate the proposed change (`parse_change`)
2. Resolve the dataset URN in DataHub (`resolve_urn`, via `search`)
3. Validate the column exists in the real schema (`validate_schema`, via
   `list_schema_fields`; Live mode only)
4. Read DataHub Memory — the last persisted ChangeGuard decision for this
   dataset, if any (`fetch_previous_context`, via `get_entities`;
   best-effort, informational only)
5. Fetch confirmed column-level downstream lineage
   (`fetch_lineage`, via `get_lineage` with a `column` filter)
6. Fetch potential table-level downstream propagation
   (`fetch_potential_downstream`, via `get_lineage` without a `column`
   filter; informational only, never scored)
7. Calculate risk score (`assess_risk`, via `contract_sentinel/risk.py`)
8. Generate the Markdown impact report (`generate_report`)
9. Optionally write the report back to DataHub (`writeback`, via the
   optional `save_document` MCP tool)
10. Render the final ALLOW/BLOCK decision (`decision`)
11. Optionally persist the decision as DataHub custom properties
    (`persist_decision`, via the DataHub SDK directly, not MCP)

Both writeback steps (9 and 11) only run if `confirm_writeback=True` is
passed to `run_async`/`run`.

## 2. Real DataHub environment

- DataHub frontend: `http://localhost:9002`
- DataHub GMS: `http://localhost:8080`
- Main dataset: `commerce.orders` (platform: `snowflake`, env: `PROD`)
  - Columns: `order_id`, `customer_id`, `amount`
- Real table-level lineage (as seeded and observed via the DataHub UI and
  MCP `get_lineage`):
  `commerce.orders` → `analytics.customer_orders` → `analytics.sales_summary`
- Real confirmed column-level lineage: only
  `commerce.orders.customer_id` → `analytics.customer_orders.customer_id`
- `analytics.sales_summary` is currently POTENTIAL, not confirmed,
  because DataHub has no explicit column-level lineage edge reaching it
  — only the table-level edge from `analytics.customer_orders`.
- Ownership, Domain, and Tags are **not set** on any of these three
  seeded datasets today (confirmed directly in the DataHub UI and via
  DataHub's GraphQL API during investigation, independent of MCP).

## 3. Verified scenarios

RENAME `commerce.orders.customer_id` → `cust_key` (Live mode):
- 50/100, MEDIUM, ALLOW
- Confirmed affected assets: 1 (`analytics.customer_orders`)
- Potential downstream assets: 1 (`analytics.sales_summary`)

DROP `commerce.orders.customer_id` (Live mode):
- 60/100, HIGH, BLOCK
- Confirmed affected assets: 1 (`analytics.customer_orders`)
- Potential downstream assets: 1 (`analytics.sales_summary`)

Both were re-verified end-to-end against the real local DataHub instance
before this handoff, including two consecutive Live runs in the same
Streamlit session and via the CLI.

## 4. CLI / CI

`contract_sentinel/cli.py` (run as `python -m contract_sentinel.cli`):

```
python -m contract_sentinel.cli \
  --dataset commerce.orders --column customer_id \
  --operation drop --mode live \
  --datahub-url http://localhost:8080 --json
```

Arguments: `--dataset`, `--column`, `--operation`
(`rename|drop|type_change|add`), `--new-name` (required for `rename`),
`--mode` (`demo|live`, default `demo`), `--datahub-url` (default
`http://localhost:8080`), `--datahub-token` (optional), `--json`.

Exit codes: `0` = ALLOW, `1` = BLOCK, `2` = execution/configuration error
(dataset not found, column not found, DataHub/MCP unreachable, invalid
arguments, e.g. `rename` without `--new-name`).

`--json` prints exactly one JSON object to stdout (no diagnostics mixed
in), even on error — errors emit `{"error": ..., "dataset": ..., ...}` to
stdout in `--json` mode, or `ChangeGuard error: ...` to stderr otherwise.

The CLI reuses `ChangeGuardAgent` and `create_live_adapter` directly (see
`contract_sentinel/cli.py`'s `_run()`) — it has no parallel risk engine.
It always calls `run_async(..., confirm_writeback=False)`, so it never
writes to DataHub and is safe to run unattended.

A conceptual GitHub Actions example exists at
`examples/github-actions-gate.yml`. It is illustrative only — it is not
wired into this repository's own Actions.

## 5. DataHub writeback

"Persist Decision to DataHub" (`persist_decision` step,
`contract_sentinel/datahub_writeback.py`, `write_decision_to_datahub`)
writes six `changeguard_*` custom properties onto the analyzed dataset
using the official `acryl-datahub` SDK's REST emitter +
`DatasetPatchBuilder`, directly against the GMS REST API (not through
MCP). The patch is additive (existing properties/description survive).

**Custom Properties represent LAST STATE ONLY.** Every write overwrites
the same six keys. There is no historical log of past decisions anywhere
in the system today — see [DataHub Memory](#6-datahub-memory).

Both writeback mechanisms (this one and the separate `save_document`
report writeback) require `confirm_writeback=True`; if not confirmed,
the corresponding step is `SKIPPED`, nothing is written.

## 6. DataHub Memory

`get_entities` is now a genuinely used tool (previously required for
connection but never called). It is used exclusively to read back
ChangeGuard's own previously persisted decision.

Real response shape (verified against a live `mcp-server-datahub`
server):

```json
[
  {
    "urn": "...",
    "properties": {
      "customProperties": [
        {"key": "changeguard_decision", "value": "BLOCK"},
        {"key": "changeguard_risk_score", "value": "60"},
        {"key": "changeguard_severity", "value": "high"},
        {"key": "changeguard_operation", "value": "drop"},
        {"key": "changeguard_column", "value": "customer_id"},
        {"key": "changeguard_timestamp", "value": "2026-08-09T02:40:44+00:00"}
      ]
    },
    "platform": {...}, "health": [...], "schemaMetadata": {...},
    "relatedDocuments": {...}
  }
]
```

`parse_persisted_context` (`contract_sentinel/datahub_writeback.py`)
parses this into a `PersistedContext`, or returns `None` if no
`changeguard_decision` key is present (nothing was ever persisted — not
an error).

If the resolved dataset/column/operation of the *current* run exactly
match the persisted context, `AgentResult.previously_evaluated = True`
and the Streamlit UI shows a **"Previously evaluated in DataHub"**
badge. The system still re-runs `validate_schema`, `fetch_lineage`, and
`assess_risk` in full — it never reuses the old score or skips any step
because of a match.

## 7. MCP tools actually used

Inspected directly in `contract_sentinel/agent.py` and
`contract_sentinel/datahub_mcp.py` (not inferred from `REQUIRED_TOOLS`):

| Tool | Actually called? | Where / why |
|---|---|---|
| `search` | Yes, every Live run | `resolve_urn` step — find the dataset by name |
| `list_schema_fields` | Yes, every Live run | `validate_schema` step — verify the column (and rename target) exist |
| `get_entities` | Yes, every Live run | `fetch_previous_context` step — read `properties.customProperties` for DataHub Memory |
| `get_lineage` | Yes, twice per Live run | `fetch_lineage` (with `column` filter, confirmed impact) and `fetch_potential_downstream` (without `column`, potential impact) |
| `save_document` | Conditionally, only if available and `confirm_writeback=True` | `writeback` step — optional Markdown report document |
| `get_lineage_paths_between` | **No** | Listed in `DataHubMCPAdapter.REQUIRED_TOOLS` so an incompatible MCP server is rejected at connect time, but never invoked in the pipeline today |

## 8. Important architecture files

- `app.py` — Streamlit UI (Demo + Live modes), renders the agent's steps
  in real time, the Results panel, DataHub Memory section, and the
  Why Flagged / Blast Radius / Checklist / Full Report tabs.
- `contract_sentinel/agent.py` — `ChangeGuardAgent`, the single pipeline
  described in section 1. Owns all step sequencing and is the only
  caller of `assess_change`.
- `contract_sentinel/risk.py` — `assess_change`, the deterministic
  scoring function (operation weights + downstream asset/critical/
  dashboard bonuses, severity thresholds). Not touched by this handoff.
- `contract_sentinel/datahub_mcp.py` — `DataHubMCPAdapter`, the typed
  boundary over the injected MCP `call_tool` callable; declares
  required/optional tools and wraps `get_lineage`/`get_entities`/
  `save_document` calls.
- `contract_sentinel/mcp_connection.py` — `MCPStdioClient` +
  `create_live_adapter`, the real stdio transport to
  `uvx mcp-server-datahub@latest`. Contains the Windows-hang fix in
  `close()` (timeout + kill fallback) — see `AGENTS.md`.
- `contract_sentinel/datahub_writeback.py` — `write_decision_to_datahub`
  (writes the six `changeguard_*` custom properties via the DataHub SDK)
  and `parse_persisted_context` (reads them back from a `get_entities`
  response). Also `build_writeback_properties`, the pure function shared
  by both directions.
- `contract_sentinel/report.py` — `render_markdown`, builds the Full
  Report Markdown (reasons, confirmed/potential blast radius, checklist,
  and the Previous ChangeGuard Context section).
- `contract_sentinel/cli.py` — the CI/CD gate CLI described in section 4.
- Tests: `tests/test_agent.py` (pipeline + real MCP response-shape
  tests, including `LiveModeShapeTests`), `tests/test_datahub_mcp.py`
  (adapter/tool-availability tests), `tests/test_datahub_writeback.py`
  (writeback property building + `parse_persisted_context`),
  `tests/test_report.py`, `tests/test_risk.py`, `tests/test_cli.py`.

## 9. Important design decisions

- Deterministic workflow: no LLM anywhere in the decision path.
- Confirmed vs Potential are strictly separated: Confirmed comes only
  from column-level `get_lineage`; Potential comes only from table-level
  `get_lineage`, with confirmed URNs excluded to avoid double-counting.
- Potential downstream propagation never affects the risk score.
- The previous persisted decision (DataHub Memory) never affects the
  current score — it is operator context only.
- No ownership/domain/tags are fabricated. The seeded local catalog has
  none set, and `get_entities`' current response shape does not expose
  them anyway.
- No GraphQL was added as a parallel enrichment path — DataHub Memory
  reads exclusively through the existing MCP `get_entities` tool.
- DataHub Memory is explicitly documented as "last persisted decision,"
  never as a history or audit trail, in code comments, the UI, the
  report, and `README.md`.
- Best-effort metadata/context reads (`fetch_previous_context`,
  `fetch_potential_downstream`) must never destroy an otherwise valid
  analysis — both degrade to an empty/`None` result and continue the
  pipeline on failure.

## 10. Known limitations

These are current and real as of this handoff (verified against the
live code, not carried over from earlier, already-fixed issues):

- DataHub Memory is last-state only; there is no historical audit trail
  of past ChangeGuard decisions anywhere in the system.
- Ownership, Domain, and Tags are not available for Live-mode assets:
  the seeded local catalog has none set, and the current
  `mcp-server-datahub` `get_entities` response does not include
  `ownership`/`domain`/`tags` fields at all.
- Potential downstream propagation depends entirely on table-level
  `get_lineage`; if DataHub's table-level lineage graph is incomplete,
  potential assets will be missed (this mirrors a real DataHub
  limitation, not a ChangeGuard bug).
- The `fetch_potential_downstream` MCP call has been observed to
  intermittently time out (`MCP request 'tools/call' timed out after
  30s`) under contention on this local dev machine. It degrades to a
  `FAILED` step without blocking the rest of the pipeline (per the
  design decision in section 9), but the timeout itself has not been
  investigated or fixed.
- Running `contract_sentinel/cli.py` in Live mode on Windows can print an
  `asyncio`/`ResourceWarning` traceback to stderr during interpreter
  shutdown (`Event loop is closed`, `I/O operation on closed pipe`). This
  happens after the real result has already been printed to stdout and
  does not affect the exit code or `--json` output, but it has not been
  cleaned up.
- No Slack/Jira integration.
- No LLM anywhere in the pipeline.
- `get_lineage_paths_between` is required to connect but is not used —
  see section 7.

## 11. Latest stable state

Stable commit before this handoff: `c36b321` ("Add DataHub-backed
decision memory"). Tests at that commit: 60/60 passing.

(If this handoff's own documentation commit is pushed after this file is
written, `git log` will show it directly above `c36b321` on `main`.)

## 12. Recommended next work

These are candidate ideas only — nothing below has been implemented, and
none of it should be assumed to exist:

1. A "Remediation Plan" / "How to Make This Safe?" feature — has not been
   designed or implemented.
2. A more polished `changeguard check` CLI command/alias — the current
   CLI is `python -m contract_sentinel.cli`; no wrapper script exists.
3. Investigate and, if safely reproducible, clean up the Windows
   `asyncio` shutdown warning noted in section 10 — not yet attempted.
4. Final demo/video preparation for the hackathon submission — not
   started as part of this codebase.
