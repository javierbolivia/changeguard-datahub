# ChangeGuard Development Rules

These are permanent repository instructions. Follow them for every task
in this codebase, including tasks requested by a human operator.

## Core principles

- `ChangeGuardAgent` (`contract_sentinel/agent.py`) is the single, central
  engine for schema-change risk analysis. It is the only place that calls
  `assess_change` (`contract_sentinel/risk.py`).
- Never duplicate scoring logic in the CLI (`contract_sentinel/cli.py`),
  Streamlit UI (`app.py`), or any other module. Every entry point must
  build a `Change`, run it through `ChangeGuardAgent`, and use the
  resulting `AgentResult` — never recompute a score independently.
- Do not change risk thresholds, operation weights, or severity bands in
  `contract_sentinel/risk.py` without explicit authorization.
- Confirmed Column Impact (`downstream_assets`, from column-level
  `get_lineage`) is the only signal that affects `assess_change` and thus
  the risk score.
- Potential Downstream Propagation (`potential_downstream_assets`, from
  table-level `get_lineage`) is informational only and must never
  increase the score or count as an affected asset.
- Never convert a potential asset into confirmed without real
  column-level lineage evidence from DataHub. De-duplication logic (skip
  an asset already in the confirmed set) already exists in
  `agent.py` — do not remove it.
- Live mode must use a real DataHub instance via MCP. Never substitute
  fixtures/demo data on a Live-mode failure — errors must surface, not be
  papered over (see the `LiveModeShapeTests` in `tests/test_agent.py`).
- Never invent datasets, columns, lineage, owners, domains, or tags.
  If real metadata is unavailable, show `Unknown` / `Not available` /
  `None` — do not fabricate values, and do not seed DataHub with
  synthetic metadata without asking first.

## Current verified behavior

RENAME `commerce.orders.customer_id` → `cust_key`:
- 50/100, MEDIUM, ALLOW
- Confirmed: `analytics.customer_orders`
- Potential: `analytics.sales_summary`

DROP `commerce.orders.customer_id`:
- 60/100, HIGH, BLOCK
- Confirmed: `analytics.customer_orders`
- Potential: `analytics.sales_summary`

These numbers come from `assess_change` given exactly one confirmed
downstream asset (`drop` = 55 base + 5 for 1 downstream asset = 60;
`rename` = 45 base + 5 = 50). Do not treat these as hardcoded — they must
still be produced by re-running the real pipeline.

## CI Gate

`contract_sentinel/cli.py` exit codes:
- `0` = ALLOW
- `1` = BLOCK
- `2` = execution/configuration error (dataset not found, column not
  found, DataHub/MCP unreachable, invalid arguments)

The CLI must keep reusing `ChangeGuardAgent` and `create_live_adapter`
exactly as the Streamlit app does. It must never write back to DataHub
(`confirm_writeback=False` always), so it stays safe to run unattended in
CI.

## DataHub Memory

DataHub Memory means **only**: the Last Persisted ChangeGuard Decision.

- It is NOT a history and NOT an audit trail — `changeguard_*` custom
  properties are overwritten on every writeback, so only the single most
  recent decision is ever readable this way.
- Read via `get_entities` → `properties.customProperties` (a list of
  `{"key": ..., "value": ...}` dicts), parsed by
  `contract_sentinel.datahub_writeback.parse_persisted_context`.
- Currently reads these six keys: `changeguard_decision`,
  `changeguard_risk_score`, `changeguard_severity`,
  `changeguard_operation`, `changeguard_column`, `changeguard_timestamp`.
- The previous context must never modify the current score. Every run
  must still validate schema, fetch lineage, and compute the score fresh
  — even when `previously_evaluated` is `True` (dataset/column/operation
  match the persisted context exactly).
- A `get_entities` failure is best-effort: degrade to
  `previous_context = None`, mark the `fetch_previous_context` step
  `FAILED`, and continue the rest of the pipeline normally.

## Writeback

- "Persist Decision" (`persist_decision` step) uses DataHub Custom
  Properties via the official `acryl-datahub` SDK, independent of MCP.
  This is what DataHub Memory reads back.
- "Write Back to DataHub" (`writeback` step) uses the optional MCP
  `save_document` tool to write the full Markdown report.
- If `save_document` is not advertised by the MCP server: the `writeback`
  step must be `SKIPPED` with a clear reason, never `FAILED`. A real
  failure during the `save_document` call itself is still `FAILED`.

## MCP reliability

The MCP subprocess lifecycle in `contract_sentinel/mcp_connection.py`
(`MCPStdioClient.close()`) already has timeout + kill-fallback protection
against Windows hangs. Do not revert this fix or remove the timeouts.

## Testing

Before declaring any task finished:
- Run the full test suite and report the exact pass count.
- Run `git status`.
- Delete any temporary/diagnostic scripts you created during the task.
- Do not leave log files behind.
- Do not leave credentials or tokens in any file.
- Do not commit or push unless explicitly instructed.
